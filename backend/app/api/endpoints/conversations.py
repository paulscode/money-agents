from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, UploadFile, File, Form
from app.core.datetime_utils import utc_now, ensure_utc
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_
from uuid import UUID
from typing import Optional
import re

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_active_user
from app.models import User, Conversation, Message, MessageRead
from app.schemas import (
    ConversationCreate, ConversationResponse,
    MessageCreate, MessageResponse,
    MarkMessagesReadRequest, UnreadCountResponse
)
from app.services.file_storage import FileStorageService


router = APIRouter()


def extract_mentions(content: str) -> list[str]:
    """Extract @username mentions from message content."""
    # Find all @username patterns (alphanumeric, underscore, hyphen)
    mentions = re.findall(r'@([\w-]+)', content)
    return list(set(mentions))  # Remove duplicates


async def get_user_ids_by_usernames(db: AsyncSession, usernames: list[str]) -> list[UUID]:
    """Get user IDs from usernames."""
    if not usernames:
        return []
    
    result = await db.execute(
        select(User.id).where(User.username.in_(usernames))
    )
    return [row[0] for row in result.all()]


async def get_unread_count_for_conversation(db: AsyncSession, conversation_id: UUID, user_id: UUID) -> int:
    """Get count of unread messages in a conversation for a user."""
    # Count messages where there's no MessageRead record for this user
    # and the message was not sent by this user
    subquery = select(MessageRead.message_id).where(
        MessageRead.user_id == user_id
    ).subquery()
    
    result = await db.execute(
        select(func.count(Message.id)).where(
            and_(
                Message.conversation_id == conversation_id,
                Message.sender_id != user_id,  # Exclude own messages
                Message.id.notin_(select(subquery))
            )
        )
    )
    return result.scalar() or 0


# ===== Conversation Endpoints =====

@router.post("/", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    conversation_data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new conversation or return existing one for the same proposal."""
    # Check if conversation already exists for this proposal
    if conversation_data.conversation_type and conversation_data.related_id:
        result = await db.execute(
            select(Conversation).where(
                and_(
                    Conversation.conversation_type == conversation_data.conversation_type,
                    Conversation.related_id == conversation_data.related_id
                )
            )
        )
        existing_conversation = result.scalar_one_or_none()
        
        if existing_conversation:
            # Return existing conversation with unread count
            response = ConversationResponse.model_validate(existing_conversation)
            response.unread_count = await get_unread_count_for_conversation(
                db, existing_conversation.id, current_user.id
            )
            return response
    
    # Create new conversation
    conversation = Conversation(
        created_by_user_id=current_user.id,
        **conversation_data.model_dump()
    )
    
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    
    response = ConversationResponse.model_validate(conversation)
    response.unread_count = 0
    return response


@router.get("/", response_model=list[ConversationResponse])
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    conversation_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all conversations (now shared, not per-user)."""
    query = select(Conversation)
    
    if conversation_type:
        query = query.where(Conversation.conversation_type == conversation_type)
    
    query = query.order_by(desc(Conversation.updated_at)).offset(skip).limit(limit)
    
    result = await db.execute(query)
    conversations = result.scalars().all()
    
    # Add unread counts to each conversation
    response_conversations = []
    for conv in conversations:
        response = ConversationResponse.model_validate(conv)
        response.unread_count = await get_unread_count_for_conversation(
            db, conv.id, current_user.id
        )
        response_conversations.append(response)
    
    return response_conversations


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a specific conversation (visible to all users)."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    response = ConversationResponse.model_validate(conversation)
    response.unread_count = await get_unread_count_for_conversation(
        db, conversation.id, current_user.id
    )
    return response


@router.delete("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Clear all messages in a conversation.
    
    This deletes all messages and their associated read records,
    but keeps the conversation itself intact.
    Useful for clearing test data while preserving the conversation link.
    """
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Get all message IDs in this conversation
    message_result = await db.execute(
        select(Message.id).where(Message.conversation_id == conversation_id)
    )
    message_ids = [row[0] for row in message_result.all()]
    
    if message_ids:
        # Delete read records for these messages
        await db.execute(
            MessageRead.__table__.delete().where(MessageRead.message_id.in_(message_ids))
        )
        
        # Delete the messages
        await db.execute(
            Message.__table__.delete().where(Message.conversation_id == conversation_id)
        )
    
    await db.commit()


# ===== Message Endpoints =====

@router.post("/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def create_message(
    conversation_id: UUID,
    message_data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new message in a conversation."""
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Extract @mentions from content
    mentioned_usernames = extract_mentions(message_data.content)
    mentioned_user_ids = await get_user_ids_by_usernames(db, mentioned_usernames)
    
    # Create message with conversation_id from URL parameter
    message_dict = message_data.model_dump()
    message_dict['conversation_id'] = conversation_id
    message_dict['sender_id'] = current_user.id
    message_dict['mentioned_user_ids'] = [str(uid) for uid in mentioned_user_ids] if mentioned_user_ids else None
    
    message = Message(**message_dict)
    
    db.add(message)
    
    # Update conversation's updated_at
    from datetime import datetime
    conversation.updated_at = utc_now()
    
    # Flush to generate the message ID
    await db.flush()
    
    # Mark message as read by the sender
    message_read = MessageRead(
        message_id=message.id,
        user_id=current_user.id
    )
    db.add(message_read)
    
    await db.commit()
    await db.refresh(message)
    
    response = MessageResponse.model_validate(message)
    response.is_read = True  # Sender always has message marked as read
    response.sender_username = current_user.username
    return response


_ALLOWED_METADATA_KEYS = {"applied_edit", "applied_edits", "reaction", "flagged", "pinned", "bookmark"}


@router.patch("/{conversation_id}/messages/{message_id}/metadata", response_model=MessageResponse)
async def update_message_metadata(
    conversation_id: UUID,
    message_id: UUID,
    metadata_update: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update metadata on a message (merge with existing metadata).
    
    This is used to persist applied edit states for proposal/tool discussions,
    so when users return to the conversation, they see which suggestions were already applied.
    """
    # Validate metadata keys
    invalid_keys = set(metadata_update.keys()) - _ALLOWED_METADATA_KEYS
    if invalid_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown metadata keys: {', '.join(sorted(invalid_keys))}"
        )
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Get the message
    result = await db.execute(
        select(Message).where(
            and_(
                Message.id == message_id,
                Message.conversation_id == conversation_id
            )
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )
    
    # Merge metadata (existing + new)
    from sqlalchemy.orm.attributes import flag_modified
    existing_meta = message.meta_data or {}
    message.meta_data = {**existing_meta, **metadata_update}
    flag_modified(message, "meta_data")
    
    await db.commit()
    await db.refresh(message)
    
    response = MessageResponse.model_validate(message)
    response.is_read = True  # User updating is reading
    if message.sender:
        response.sender_username = message.sender.username
    return response


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all messages in a conversation (visible to all users)."""
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Get messages with sender info
    from sqlalchemy.orm import selectinload
    query = select(Message).where(
        Message.conversation_id == conversation_id
    ).options(selectinload(Message.sender)).order_by(Message.created_at).offset(skip).limit(limit)
    
    result = await db.execute(query)
    messages = result.scalars().all()
    
    # Get which messages the current user has read
    read_result = await db.execute(
        select(MessageRead.message_id).where(
            and_(
                MessageRead.user_id == current_user.id,
                MessageRead.message_id.in_([msg.id for msg in messages])
            )
        )
    )
    read_message_ids = {row[0] for row in read_result.all()}
    
    # Build responses with read status and sender username
    response_messages = []
    for msg in messages:
        response = MessageResponse.model_validate(msg)
        response.is_read = msg.id in read_message_ids
        if msg.sender:
            response.sender_username = msg.sender.username
        response_messages.append(response)
    
    return response_messages


@router.get("/proposals/unread-counts/all", response_model=list[UnreadCountResponse])
async def get_all_proposals_unread_counts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get unread message counts for all proposals."""
    # Get all proposal conversations
    result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_type == "proposal"
        )
    )
    conversations = result.scalars().all()
    
    # Calculate unread count for each
    unread_counts = []
    for conv in conversations:
        if conv.related_id:
            unread_count = await get_unread_count_for_conversation(
                db, conv.id, current_user.id
            )
            if unread_count > 0:  # Only include if there are unread messages
                unread_counts.append(
                    UnreadCountResponse(
                        proposal_id=conv.related_id,
                        unread_count=unread_count
                    )
                )
    
    return unread_counts


@router.post("/{conversation_id}/messages/mark-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_messages_read(
    conversation_id: UUID,
    request: MarkMessagesReadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Mark messages as read for the current user."""
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Mark each message as read (skip if already marked)
    for message_id in request.message_ids:
        # Check if already marked as read
        existing = await db.execute(
            select(MessageRead).where(
                and_(
                    MessageRead.message_id == message_id,
                    MessageRead.user_id == current_user.id
                )
            )
        )
        if not existing.scalar_one_or_none():
            message_read = MessageRead(
                message_id=message_id,
                user_id=current_user.id
            )
            db.add(message_read)
    
    await db.commit()


@router.get("/proposals/{proposal_id}/unread-count", response_model=UnreadCountResponse)
async def get_proposal_unread_count(
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get unread message count for a specific proposal."""
    # Find conversation for this proposal
    result = await db.execute(
        select(Conversation).where(
            and_(
                Conversation.conversation_type == "proposal",
                Conversation.related_id == proposal_id
            )
        )
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        return UnreadCountResponse(proposal_id=proposal_id, unread_count=0)
    
    unread_count = await get_unread_count_for_conversation(
        db, conversation.id, current_user.id
    )
    
    return UnreadCountResponse(proposal_id=proposal_id, unread_count=unread_count)


@router.get("/tools/{tool_id}/unread-count", response_model=UnreadCountResponse)
async def get_tool_unread_count(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get unread message count for a specific tool."""
    # Find conversation for this tool
    result = await db.execute(
        select(Conversation).where(
            and_(
                Conversation.conversation_type == "tool",
                Conversation.related_id == tool_id
            )
        )
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        return UnreadCountResponse(tool_id=tool_id, unread_count=0)
    
    unread_count = await get_unread_count_for_conversation(
        db, conversation.id, current_user.id
    )
    
    return UnreadCountResponse(tool_id=tool_id, unread_count=unread_count)


@router.get("/tools/unread-counts/all", response_model=list[UnreadCountResponse])
async def get_all_tools_unread_counts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get unread message counts for all tools with conversations."""
    # Get all tool conversations
    result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_type == "tool"
        )
    )
    conversations = result.scalars().all()
    
    unread_counts = []
    for conversation in conversations:
        if conversation.related_id:
            unread_count = await get_unread_count_for_conversation(
                db, conversation.id, current_user.id
            )
            unread_counts.append(
                UnreadCountResponse(
                    tool_id=conversation.related_id,
                    unread_count=unread_count
                )
            )
    
    return unread_counts


# ===== File Upload/Download Endpoints =====

@router.post("/{conversation_id}/messages/upload", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_message_with_file(
    request: Request,
    conversation_id: UUID,
    content: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a message with a file attachment."""
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Extract @mentions from content
    mentioned_usernames = extract_mentions(content)
    mentioned_user_ids = await get_user_ids_by_usernames(db, mentioned_usernames)
    
    # Create message first (we need the message ID for file storage)
    message = Message(
        conversation_id=conversation_id,
        sender_type="user",
        sender_id=current_user.id,
        content=content,
        content_format="markdown",
        mentioned_user_ids=[str(uid) for uid in mentioned_user_ids] if mentioned_user_ids else None,
        attachments=[]
    )
    
    db.add(message)
    
    # Flush to generate the message ID
    await db.flush()
    
    # Save file and get metadata
    try:
        file_metadata = await FileStorageService.save_file(
            file=file,
            conversation_id=conversation_id,
            message_id=message.id
        )
        
        # Add file metadata to message attachments
        message.attachments = [file_metadata]
        
    except HTTPException as e:
        # Rollback if file upload fails
        await db.rollback()
        raise e
    
    # Update conversation's updated_at
    from datetime import datetime
    conversation.updated_at = utc_now()
    
    # Mark message as read by the sender
    message_read = MessageRead(
        message_id=message.id,
        user_id=current_user.id
    )
    db.add(message_read)
    
    await db.commit()
    await db.refresh(message)
    
    response = MessageResponse.model_validate(message)
    response.is_read = True
    response.sender_username = current_user.username
    return response


@router.get("/files/{file_id}")
async def download_file(
    file_id: str,
    conversation_id: UUID = Query(..., description="Conversation ID for permission check"),
    message_id: UUID = Query(..., description="Message ID for file location"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Download a file attachment."""
    # Verify conversation exists and user has access
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Verify message exists in this conversation
    result = await db.execute(
        select(Message).where(
            and_(
                Message.id == message_id,
                Message.conversation_id == conversation_id
            )
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )
    
    # Get file path
    file_path = await FileStorageService.get_file_path(
        file_id=file_id,
        conversation_id=conversation_id,
        message_id=message_id
    )
    
    if not file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Return file
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/octet-stream"
    )


@router.delete("/messages/{message_id}/attachments/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    message_id: UUID,
    file_id: str,
    conversation_id: UUID = Query(..., description="Conversation ID for permission check"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a file attachment from a message. Admin only."""
    # Only admins can delete attachments
    if current_user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can delete attachments"
        )
    
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Get the message
    result = await db.execute(
        select(Message).where(
            and_(
                Message.id == message_id,
                Message.conversation_id == conversation_id
            )
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )
    
    # Remove attachment from message's attachments array
    if message.attachments:
        message.attachments = [
            att for att in message.attachments 
            if att.get('id') != file_id
        ]
        await db.commit()
    
    # Delete the physical file
    await FileStorageService.delete_file(
        file_id=file_id,
        conversation_id=conversation_id,
        message_id=message_id
    )
    
    # Also delete thumbnail if it exists
    if not file_id.endswith('_thumb'):
        await FileStorageService.delete_file(
            file_id=f"{file_id}_thumb",
            conversation_id=conversation_id,
            message_id=message_id
        )
    
    return None
