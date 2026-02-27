"""
File storage service for handling file uploads and downloads.

Stores files in: uploads/conversations/{conversation_id}/{message_id}/{file_id}{ext}
"""

import aiofiles
import magic
from pathlib import Path
from uuid import UUID, uuid4
from typing import Optional
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from fastapi import UploadFile, HTTPException
from PIL import Image
import io
import logging

from app.core.config import settings


# Base upload directory
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"

# Allowed file types (MIME types)
ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    
    # Audio
    "audio/mpeg",  # MP3
    "audio/mp4",   # M4A
    "audio/wav",
    "audio/ogg",
    "audio/webm",
    
    # Video
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/quicktime",  # MOV
    
    # Documents
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    
    # Archives
    "application/zip",
    "application/x-tar",
    "application/gzip",
    
    # Office documents
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # XLSX
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # PPTX
    
    # Code files
    "text/x-python",
    "text/x-java",
    "text/javascript",
    "application/json",
    "text/html",
    "text/css",
}

# Max file size: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024


class FileStorageService:
    """Service for managing file uploads and downloads."""
    
    @staticmethod
    async def save_file(
        file: UploadFile,
        conversation_id: UUID,
        message_id: UUID
    ) -> dict:
        """
        Save an uploaded file and return metadata.
        
        Args:
            file: The uploaded file
            conversation_id: ID of the conversation
            message_id: ID of the message
            
        Returns:
            dict: File metadata including id, filename, size, mime_type, url
            
        Raises:
            HTTPException: If file validation fails
        """
        # GAP-20: Stream file in chunks to prevent memory exhaustion from
        # concurrent large uploads. Abort early when limit exceeded instead
        # of reading the entire file into memory before checking.
        CHUNK_SIZE = 1024 * 1024  # 1 MB
        chunks: list[bytes] = []
        file_size = 0
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size is {MAX_FILE_SIZE / 1024 / 1024:.0f}MB"
                )
            chunks.append(chunk)
        content = b"".join(chunks)
        
        # Detect MIME type
        mime_type = magic.from_buffer(content, mime=True)
        
        # Validate MIME type
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed: {mime_type}. Allowed types: images, audio, video, documents, archives"
            )
        
        # Generate unique file ID
        file_id = uuid4()
        
        # Get file extension from original filename
        ext = Path(file.filename).suffix if file.filename else ""
        
        # Create storage path
        storage_dir = UPLOAD_DIR / "conversations" / str(conversation_id) / str(message_id)
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{file_id}{ext}"
        file_path = storage_dir / filename
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)
        
        # Generate thumbnail for images (except SVG which is vector)
        thumbnail_url = None
        if mime_type.startswith('image/') and mime_type != 'image/svg+xml':
            thumbnail_url = await FileStorageService._generate_thumbnail(
                content, file_id, storage_dir, ext
            )
        
        # Return metadata
        return {
            "id": str(file_id),
            "filename": file.filename or filename,
            "size": file_size,
            "mime_type": mime_type,
            "uploaded_at": utc_now().isoformat(),
            "thumbnail_url": thumbnail_url
        }
    
    @staticmethod
    async def _generate_thumbnail(
        image_content: bytes,
        file_id: UUID,
        storage_dir: Path,
        ext: str,
        max_size: tuple = (300, 300)
    ) -> Optional[str]:
        """
        Generate a thumbnail for an image.
        
        Args:
            image_content: Original image bytes
            file_id: ID of the file
            storage_dir: Directory to store thumbnail
            ext: File extension
            max_size: Maximum thumbnail dimensions (width, height)
            
        Returns:
            Relative URL to thumbnail, or None if generation fails
        """
        try:
            # Open image
            img = Image.open(io.BytesIO(image_content))
            
            # Convert RGBA to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            
            # Generate thumbnail
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Save thumbnail
            thumbnail_filename = f"{file_id}_thumb.jpg"
            thumbnail_path = storage_dir / thumbnail_filename
            img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)
            
            return str(file_id) + "_thumb"
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to generate thumbnail: {e}")
            return None
    
    @staticmethod
    def _validate_file_id(file_id: str) -> bool:
        """Validate file_id is a safe format (UUID with optional _thumb suffix).

        Prevents glob metacharacters and path traversal in file_id.
        """
        import re
        # UUID (hex-digits + hyphens) with optional _thumb suffix
        return bool(re.fullmatch(r'[0-9a-fA-F\-]{32,36}(_thumb)?', file_id))

    @staticmethod
    async def get_file_path(file_id: str, conversation_id: UUID, message_id: UUID) -> Optional[Path]:
        """
        Get the full path to a file.
        
        Args:
            file_id: ID of the file (can be UUID string or thumbnail ID like 'uuid_thumb')
            conversation_id: ID of the conversation
            message_id: ID of the message
            
        Returns:
            Path to the file if it exists, None otherwise
        """
        # Validate file_id format to prevent glob injection / path traversal
        if not FileStorageService._validate_file_id(file_id):
            return None

        storage_dir = UPLOAD_DIR / "conversations" / str(conversation_id) / str(message_id)
        
        # Look for file with any extension
        for file_path in storage_dir.glob(f"{file_id}.*"):
            if file_path.is_file():
                # Defence-in-depth: verify resolved path stays within storage_dir
                if file_path.resolve().parent == storage_dir.resolve():
                    return file_path
        
        # Also check without extension
        file_path = storage_dir / str(file_id)
        if file_path.is_file() and file_path.resolve().parent == storage_dir.resolve():
            return file_path
        
        return None
    
    @staticmethod
    async def delete_file(file_id: str, conversation_id: UUID, message_id: UUID) -> bool:
        """
        Delete a file from storage.
        
        Args:
            file_id: ID of the file (can be UUID string or thumbnail ID)
            conversation_id: ID of the conversation
            message_id: ID of the message
            
        Returns:
            True if file was deleted, False if not found
        """
        file_path = await FileStorageService.get_file_path(file_id, conversation_id, message_id)
        
        if file_path and file_path.exists():
            file_path.unlink()
            return True
        
        return False
    
    @staticmethod
    async def delete_message_files(conversation_id: UUID, message_id: UUID) -> int:
        """
        Delete all files associated with a message.
        
        Args:
            conversation_id: ID of the conversation
            message_id: ID of the message
            
        Returns:
            Number of files deleted
        """
        storage_dir = UPLOAD_DIR / "conversations" / str(conversation_id) / str(message_id)
        
        if not storage_dir.exists():
            return 0
        
        count = 0
        for file_path in storage_dir.iterdir():
            if file_path.is_file():
                file_path.unlink()
                count += 1
        
        # Remove empty directory
        try:
            storage_dir.rmdir()
        except OSError:
            pass  # Directory not empty or doesn't exist
        
        return count
    
    @staticmethod
    async def delete_conversation_files(conversation_id: UUID) -> int:
        """
        Delete all files associated with a conversation.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            Number of files deleted
        """
        storage_dir = UPLOAD_DIR / "conversations" / str(conversation_id)
        
        if not storage_dir.exists():
            return 0
        
        count = 0
        for message_dir in storage_dir.iterdir():
            if message_dir.is_dir():
                for file_path in message_dir.iterdir():
                    if file_path.is_file():
                        file_path.unlink()
                        count += 1
                try:
                    message_dir.rmdir()
                except OSError:
                    pass
        
        # Remove conversation directory
        try:
            storage_dir.rmdir()
        except OSError:
            pass
        
        return count


# Create uploads directory on import
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
