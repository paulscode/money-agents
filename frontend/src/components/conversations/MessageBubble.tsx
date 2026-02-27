import { useState, useMemo } from 'react';
import type { Message, Proposal, Tool } from '@/types';
import { Bot, User, Info, Check, Loader2, Edit3 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';
import { useAuthStore } from '@/stores/auth';
import MessageAttachments from './MessageAttachments';
import { 
  parseProposalEdits, 
  removeEditTags, 
  EDITABLE_FIELDS,
  formatFieldValue,
  convertEditValue,
  type ProposalEdit 
} from '@/lib/proposal-edit-parser';
import {
  parseToolEdits,
  removeToolEditTags,
  TOOL_EDITABLE_FIELDS,
  formatToolFieldValue,
  convertToolEditValue,
  type ToolEdit
} from '@/lib/tool-edit-parser';
import { logError } from '@/lib/logger';

interface MessageBubbleProps {
  message: Message;
  proposal?: Proposal;
  tool?: Tool;
  onAttachmentDeleted?: () => void;
  onApplyEdit?: (field: string, value: string | number) => Promise<void>;
  onEditApplied?: (fields: string | string[]) => void; // Callback to persist applied edit(s) to message metadata
}

function EditSuggestionCard({
  edit, 
  proposal,
  onApply,
  isApplying,
  isApplied,
}: { 
  edit: ProposalEdit;
  proposal?: Proposal;
  onApply: () => void;
  isApplying: boolean;
  isApplied: boolean;
}) {
  const fieldInfo = EDITABLE_FIELDS[edit.field];
  if (!fieldInfo) return null;

  // Get current value from proposal
  const currentValue = proposal ? (proposal as Record<string, unknown>)[edit.field] : undefined;
  const formattedCurrent = currentValue !== undefined 
    ? formatFieldValue(edit.field, String(currentValue))
    : 'Not set';
  const formattedNew = formatFieldValue(edit.field, edit.value);

  return (
    <div className={`mt-3 p-3 rounded-lg border ${
      isApplied 
        ? 'bg-green-500/10 border-green-500/30' 
        : 'bg-neon-purple/10 border-neon-purple/30'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Edit3 className="h-4 w-4 text-neon-purple" />
          <span className="text-sm font-medium text-gray-200">
            Edit: {fieldInfo.label}
          </span>
        </div>
        {isApplied ? (
          <span className="flex items-center gap-1 text-xs text-green-400">
            <Check className="h-3 w-3" />
            Applied
          </span>
        ) : (
          <button
            onClick={onApply}
            disabled={isApplying}
            className="px-2 py-1 text-xs bg-neon-purple/20 text-neon-purple border border-neon-purple/30 rounded hover:bg-neon-purple/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
          >
            {isApplying ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Applying...
              </>
            ) : (
              <>
                <Check className="h-3 w-3" />
                Apply
              </>
            )}
          </button>
        )}
      </div>
      
      <div className="space-y-1 text-xs">
        <div className="flex gap-2">
          <span className="text-gray-500 w-16">Current:</span>
          <span className="text-gray-400 line-through">{formattedCurrent}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-gray-500 w-16">New:</span>
          <span className="text-neon-cyan">{formattedNew}</span>
        </div>
      </div>
    </div>
  );
}

function ToolEditSuggestionCard({ 
  edit, 
  tool,
  onApply,
  isApplying,
  isApplied,
}: { 
  edit: ToolEdit;
  tool?: Tool;
  onApply: () => void;
  isApplying: boolean;
  isApplied: boolean;
}) {
  const fieldInfo = TOOL_EDITABLE_FIELDS[edit.field];
  if (!fieldInfo) return null;

  // Get current value from tool
  const currentValue = tool ? (tool as Record<string, unknown>)[edit.field] : undefined;
  const formattedCurrent = currentValue !== undefined 
    ? formatToolFieldValue(edit.field, String(currentValue))
    : 'Not set';
  const formattedNew = formatToolFieldValue(edit.field, edit.value);

  return (
    <div className={`mt-3 p-3 rounded-lg border ${
      isApplied 
        ? 'bg-green-500/10 border-green-500/30' 
        : 'bg-neon-cyan/10 border-neon-cyan/30'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Edit3 className="h-4 w-4 text-neon-cyan" />
          <span className="text-sm font-medium text-gray-200">
            Edit: {fieldInfo.label}
          </span>
        </div>
        {isApplied ? (
          <span className="flex items-center gap-1 text-xs text-green-400">
            <Check className="h-3 w-3" />
            Applied
          </span>
        ) : (
          <button
            onClick={onApply}
            disabled={isApplying}
            className="px-2 py-1 text-xs bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30 rounded hover:bg-neon-cyan/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
          >
            {isApplying ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Applying...
              </>
            ) : (
              <>
                <Check className="h-3 w-3" />
                Apply
              </>
            )}
          </button>
        )}
      </div>
      
      <div className="space-y-1 text-xs">
        <div className="flex gap-2">
          <span className="text-gray-500 w-16">Current:</span>
          <span className="text-gray-400 line-through">{formattedCurrent}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-gray-500 w-16">New:</span>
          <span className="text-neon-cyan">{formattedNew}</span>
        </div>
      </div>
    </div>
  );
}

export function MessageBubble({ message, proposal, tool, onAttachmentDeleted, onApplyEdit, onEditApplied }: MessageBubbleProps) {
  const { user: currentUser } = useAuthStore();
  const isCurrentUser = message.sender_id === currentUser?.id;
  
  const isUser = message.sender_type === 'user';
  const isAgent = message.sender_type === 'agent';
  const isSystem = message.sender_type === 'system';

  // Parse edits from agent messages (proposal edits or tool edits)
  const proposalEdits = isAgent && proposal ? parseProposalEdits(message.content) : [];
  const toolEdits = isAgent && tool ? parseToolEdits(message.content) : [];
  
  // Clean content - remove both proposal and tool edit tags
  let cleanContent = message.content;
  if (isAgent) {
    if (proposalEdits.length > 0) {
      cleanContent = removeEditTags(cleanContent);
    }
    if (toolEdits.length > 0) {
      cleanContent = removeToolEditTags(cleanContent);
    }
  }
  
  // Get persisted applied edits from message metadata (survives page reloads)
  const persistedAppliedEdits = useMemo(() => {
    const applied = message.metadata?.applied_edits;
    return new Set<string>(Array.isArray(applied) ? applied : []);
  }, [message.metadata]);
  
  // Track which edits are being applied (local state for UI loading state)
  const [applyingEdit, setApplyingEdit] = useState<string | null>(null);
  // Track edits applied this session (optimistic update while metadata saves)
  const [sessionAppliedEdits, setSessionAppliedEdits] = useState<Set<string>>(new Set());
  
  // Combined set of applied edits (persisted + session)
  const appliedEdits = useMemo(() => {
    return new Set([...persistedAppliedEdits, ...sessionAppliedEdits]);
  }, [persistedAppliedEdits, sessionAppliedEdits]);

  // Track bulk apply state
  const [isApplyingAll, setIsApplyingAll] = useState(false);

  const handleApplyProposalEdit = async (edit: ProposalEdit) => {
    if (!onApplyEdit) return;
    
    setApplyingEdit(edit.field);
    try {
      const value = convertEditValue(edit.field, edit.value);
      await onApplyEdit(edit.field, value);
      // Optimistic update
      setSessionAppliedEdits(prev => new Set([...prev, edit.field]));
      // Persist to message metadata
      onEditApplied?.(edit.field);
    } catch (error) {
      logError('Failed to apply edit:', error);
    } finally {
      setApplyingEdit(null);
    }
  };

  const handleApplyToolEdit = async (edit: ToolEdit) => {
    if (!onApplyEdit) return;
    
    setApplyingEdit(edit.field);
    try {
      const value = convertToolEditValue(edit.field, edit.value);
      await onApplyEdit(edit.field, value);
      // Optimistic update
      setSessionAppliedEdits(prev => new Set([...prev, edit.field]));
      // Persist to message metadata
      onEditApplied?.(edit.field);
    } catch (error) {
      logError('Failed to apply tool edit:', error);
    } finally {
      setApplyingEdit(null);
    }
  };

  // Apply all unapplied proposal edits
  const handleApplyAllProposalEdits = async () => {
    if (!onApplyEdit) return;
    
    const unappliedEdits = proposalEdits.filter(edit => !appliedEdits.has(edit.field));
    if (unappliedEdits.length === 0) return;
    
    setIsApplyingAll(true);
    const appliedFields: string[] = [];
    try {
      for (const edit of unappliedEdits) {
        const value = convertEditValue(edit.field, edit.value);
        await onApplyEdit(edit.field, value);
        setSessionAppliedEdits(prev => new Set([...prev, edit.field]));
        appliedFields.push(edit.field);
      }
      // Persist all applied fields at once to avoid race condition
      if (appliedFields.length > 0) {
        onEditApplied?.(appliedFields);
      }
    } catch (error) {
      logError('Failed to apply all edits:', error);
      // Still persist any fields that were successfully applied before the error
      if (appliedFields.length > 0) {
        onEditApplied?.(appliedFields);
      }
    } finally {
      setIsApplyingAll(false);
    }
  };

  // Apply all unapplied tool edits
  const handleApplyAllToolEdits = async () => {
    if (!onApplyEdit) return;
    
    const unappliedEdits = toolEdits.filter(edit => !appliedEdits.has(edit.field));
    if (unappliedEdits.length === 0) return;
    
    setIsApplyingAll(true);
    const appliedFields: string[] = [];
    try {
      for (const edit of unappliedEdits) {
        const value = convertToolEditValue(edit.field, edit.value);
        await onApplyEdit(edit.field, value);
        setSessionAppliedEdits(prev => new Set([...prev, edit.field]));
        appliedFields.push(edit.field);
      }
      // Persist all applied fields at once to avoid race condition
      if (appliedFields.length > 0) {
        onEditApplied?.(appliedFields);
      }
    } catch (error) {
      logError('Failed to apply all tool edits:', error);
      // Still persist any fields that were successfully applied before the error
      if (appliedFields.length > 0) {
        onEditApplied?.(appliedFields);
      }
    } finally {
      setIsApplyingAll(false);
    }
  };

  // Count unapplied edits
  const unappliedProposalCount = proposalEdits.filter(edit => !appliedEdits.has(edit.field)).length;
  const unappliedToolCount = toolEdits.filter(edit => !appliedEdits.has(edit.field)).length;

  // Determine alignment and styling based on sender
  const alignmentClass = isCurrentUser ? 'justify-end' : 'justify-start';
  const bubbleClass = isCurrentUser
    ? 'bg-gradient-to-br from-neon-purple/20 to-neon-pink/20 border-neon-purple/40'
    : isAgent
    ? 'bg-gradient-to-br from-neon-cyan/10 to-neon-blue/10 border-neon-cyan/40'
    : 'bg-gray-800/50 border-gray-600/40';

  const iconColor = isCurrentUser ? 'text-neon-purple' : isAgent ? 'text-neon-cyan' : 'text-gray-400';

  const Icon = isUser ? User : isAgent ? Bot : Info;
  
  // Determine display name
  const displayName = isSystem 
    ? 'System' 
    : isAgent 
    ? 'AI Agent' 
    : isCurrentUser 
    ? 'You' 
    : message.sender_username || 'Unknown User';

  return (
    <div className={`flex ${alignmentClass} mb-4 group`}>
      <div className={`max-w-[75%] flex ${isCurrentUser ? 'flex-row-reverse' : 'flex-row'} gap-3`}>
        {/* Avatar */}
        <div className={`flex-shrink-0 w-8 h-8 rounded-full ${
          isCurrentUser
            ? 'bg-gradient-to-br from-neon-purple/30 to-neon-pink/30 border border-neon-purple/50' 
            : isAgent
            ? 'bg-gradient-to-br from-neon-cyan/30 to-neon-blue/30 border border-neon-cyan/50'
            : 'bg-gray-700/50 border border-gray-600/50'
        } flex items-center justify-center`}>
          <Icon className={`h-4 w-4 ${iconColor}`} />
        </div>

        {/* Message Content */}
        <div className="flex flex-col gap-1 min-w-0">
          {/* Sender Label & Timestamp */}
          <div className={`flex items-center gap-2 text-xs ${isCurrentUser ? 'flex-row-reverse' : 'flex-row'}`}>
            <span className={`font-medium ${iconColor}`}>
              {displayName}
            </span>
            <span className="text-gray-500">
              {formatDistanceToNow(new Date(message.created_at), { addSuffix: true })}
            </span>
            {message.model_used && (
              <span className="text-gray-600 text-[10px] px-1.5 py-0.5 rounded bg-gray-800/50">
                {message.model_used}
              </span>
            )}
            {proposalEdits.length > 0 && (
              <span className="text-neon-purple text-[10px] px-1.5 py-0.5 rounded bg-neon-purple/10 border border-neon-purple/30">
                {proposalEdits.length} edit{proposalEdits.length > 1 ? 's' : ''}
              </span>
            )}
            {toolEdits.length > 0 && (
              <span className="text-neon-cyan text-[10px] px-1.5 py-0.5 rounded bg-neon-cyan/10 border border-neon-cyan/30">
                {toolEdits.length} edit{toolEdits.length > 1 ? 's' : ''}
              </span>
            )}
          </div>

          {/* Message Bubble */}
          <div className={`
            rounded-lg border px-4 py-3 
            ${bubbleClass}
            shadow-lg
            transition-all duration-200
            group-hover:shadow-xl
            ${isAgent ? 'group-hover:shadow-neon-cyan/20' : ''}
            ${isUser ? 'group-hover:shadow-neon-purple/20' : ''}
          `}>
            {message.content_format === 'markdown' ? (
              <div data-color-mode="dark" className="prose prose-invert prose-sm max-w-none">
                <SanitizedMarkdown source={cleanContent} />
              </div>
            ) : (
              <p className="text-gray-200 whitespace-pre-wrap text-sm leading-relaxed">
                {cleanContent}
              </p>
            )}
            
            {/* Proposal Edit Suggestions */}
            {proposalEdits.length > 0 && onApplyEdit && (
              <div className="space-y-2">
                {proposalEdits.map((edit, idx) => (
                  <EditSuggestionCard
                    key={`proposal-${edit.field}-${idx}`}
                    edit={edit}
                    proposal={proposal}
                    onApply={() => handleApplyProposalEdit(edit)}
                    isApplying={applyingEdit === edit.field || isApplyingAll}
                    isApplied={appliedEdits.has(edit.field)}
                  />
                ))}
                {/* Apply All button for proposal edits */}
                {unappliedProposalCount > 1 && (
                  <div className="flex gap-2 mt-2 pt-2 border-t border-neon-purple/20">
                    <button
                      onClick={handleApplyAllProposalEdits}
                      disabled={isApplyingAll || applyingEdit !== null}
                      className="flex-1 px-3 py-2 text-sm bg-neon-purple/20 text-neon-purple border border-neon-purple/30 rounded hover:bg-neon-purple/30 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {isApplyingAll ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Applying...
                        </>
                      ) : (
                        <>
                          <Check className="h-4 w-4" />
                          Apply All ({unappliedProposalCount})
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            )}
            
            {/* Tool Edit Suggestions */}
            {toolEdits.length > 0 && onApplyEdit && (
              <div className="space-y-2">
                {toolEdits.map((edit, idx) => (
                  <ToolEditSuggestionCard
                    key={`tool-${edit.field}-${idx}`}
                    edit={edit}
                    tool={tool}
                    onApply={() => handleApplyToolEdit(edit)}
                    isApplying={applyingEdit === edit.field || isApplyingAll}
                    isApplied={appliedEdits.has(edit.field)}
                  />
                ))}
                {/* Apply All button for tool edits */}
                {unappliedToolCount > 1 && (
                  <div className="flex gap-2 mt-2 pt-2 border-t border-neon-cyan/20">
                    <button
                      onClick={handleApplyAllToolEdits}
                      disabled={isApplyingAll || applyingEdit !== null}
                      className="flex-1 px-3 py-2 text-sm bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30 rounded hover:bg-neon-cyan/30 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {isApplyingAll ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Applying...
                        </>
                      ) : (
                        <>
                          <Check className="h-4 w-4" />
                          Apply All ({unappliedToolCount})
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            )}
            
            {/* File Attachments */}
            {message.attachments && message.attachments.length > 0 && (
              <MessageAttachments
                attachments={message.attachments}
                conversationId={message.conversation_id}
                messageId={message.id}
                onAttachmentDeleted={onAttachmentDeleted}
              />
            )}

            {/* Token usage indicator */}
            {message.tokens_used && (
              <div className="mt-2 pt-2 border-t border-gray-700/50">
                <span className="text-[10px] text-gray-500">
                  {message.tokens_used.toLocaleString()} tokens
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
