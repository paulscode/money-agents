import { Download, FileText, Image, Music, Video, File as FileIcon, Trash2 } from 'lucide-react';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';
import { useEffect, useState } from 'react';
import { useAuthStore } from '@/stores/auth';
import { logError } from '@/lib/logger';

interface FileAttachment {
  id: string;
  filename: string;
  size: number;
  mime_type: string;
  uploaded_at: string;
}

interface MessageAttachmentsProps {
  attachments: FileAttachment[];
  conversationId: string;
  messageId: string;
  onAttachmentDeleted?: () => void;
}

const MessageAttachments = ({ attachments, conversationId, messageId, onAttachmentDeleted }: MessageAttachmentsProps) => {
  const [mediaBlobUrls, setMediaBlobUrls] = useState<Record<string, string>>({});
  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';

  // Fetch images, audio, and video files and create blob URLs for authenticated access
  useEffect(() => {
    const fetchMedia = async () => {
      const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const urls: Record<string, string> = {};

      for (const attachment of attachments) {
        // Fetch blob URLs for images, audio, and video
        if (attachment.mime_type.startsWith('image/') || 
            attachment.mime_type.startsWith('audio/') || 
            attachment.mime_type.startsWith('video/')) {
          
          // For images, use thumbnail if available
          const fileId = attachment.mime_type.startsWith('image/') && attachment.thumbnail_url 
            ? attachment.thumbnail_url 
            : attachment.id;
          
          const fileUrl = `${API_BASE_URL}/api/v1/conversations/files/${fileId}?conversation_id=${conversationId}&message_id=${messageId}`;
          
          try {
            const response = await fetch(fileUrl, {
              headers: {
                'Authorization': `Bearer ${token}`
              }
            });
            
            if (response.ok) {
              const blob = await response.blob();
              urls[attachment.id] = URL.createObjectURL(blob);
            }
          } catch (error) {
            logError('Failed to load media:', error);
          }
        }
      }

      setMediaBlobUrls(urls);
    };

    fetchMedia();

    // Cleanup blob URLs when component unmounts
    return () => {
      Object.values(mediaBlobUrls).forEach(url => URL.revokeObjectURL(url));
    };
  }, [attachments, conversationId, messageId]);

  if (!attachments || attachments.length === 0) {
    return null;
  }

  const getFileIcon = (mimeType: string) => {
    if (mimeType.startsWith('image/')) return <Image className="w-5 h-5 text-blue-500" />;
    if (mimeType.startsWith('audio/')) return <Music className="w-5 h-5 text-purple-500" />;
    if (mimeType.startsWith('video/')) return <Video className="w-5 h-5 text-red-500" />;
    if (mimeType === 'application/pdf') return <FileText className="w-5 h-5 text-red-600" />;
    return <FileIcon className="w-5 h-5 text-gray-500" />;
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleDownload = async (fileId: string, filename: string) => {
    try {
      const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const response = await fetch(
        `${API_BASE_URL}/api/v1/conversations/files/${fileId}?conversation_id=${conversationId}&message_id=${messageId}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      );

      if (!response.ok) {
        throw new Error('Failed to download file');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      logError('Error downloading file:', error);
      alert('Failed to download file. Please try again.');
    }
  };

  const handleDelete = async (fileId: string, filename: string) => {
    if (!confirm(`Are you sure you want to delete "${filename}"? This action cannot be undone.`)) {
      return;
    }

    try {
      const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const response = await fetch(
        `${API_BASE_URL}/api/v1/conversations/messages/${messageId}/attachments/${fileId}?conversation_id=${conversationId}`,
        {
          method: 'DELETE',
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Failed to delete attachment');
      }

      alert('Attachment deleted successfully');
      
      // Notify parent component to refresh
      if (onAttachmentDeleted) {
        onAttachmentDeleted();
      }
    } catch (error) {
      logError('Error deleting attachment:', error);
      alert(`Failed to delete attachment: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const isImageFile = (mimeType: string) => mimeType.startsWith('image/');
  const isAudioFile = (mimeType: string) => mimeType.startsWith('audio/');
  const isVideoFile = (mimeType: string) => mimeType.startsWith('video/');

  const renderPreview = (attachment: FileAttachment) => {
    const fileUrl = `${API_BASE_URL}/api/v1/conversations/files/${attachment.id}?conversation_id=${conversationId}&message_id=${messageId}`;

    if (isImageFile(attachment.mime_type)) {
      const blobUrl = mediaBlobUrls[attachment.id];
      
      return (
        <div className="mt-2 mb-2">
          {blobUrl ? (
            <img
              src={blobUrl}
              alt={attachment.filename}
              className="max-w-sm max-h-64 rounded-lg border border-gray-700 cursor-pointer hover:opacity-90 transition-opacity"
              onClick={() => handleDownload(attachment.id, attachment.filename)}
              title="Click to download full size"
            />
          ) : (
            <div className="max-w-sm h-32 rounded-lg border border-gray-700 bg-gray-800 flex items-center justify-center">
              <span className="text-gray-500 text-sm">Loading image...</span>
            </div>
          )}
        </div>
      );
    }

    if (isAudioFile(attachment.mime_type)) {
      const blobUrl = mediaBlobUrls[attachment.id];
      
      return (
        <div className="mt-2 mb-2">
          {blobUrl ? (
            <audio controls className="max-w-sm">
              <source src={blobUrl} type={attachment.mime_type} />
              Your browser does not support the audio element.
            </audio>
          ) : (
            <div className="flex items-center space-x-2 text-gray-500 text-sm">
              <Music className="w-4 h-4 animate-pulse" />
              <span>Loading audio...</span>
            </div>
          )}
        </div>
      );
    }

    if (isVideoFile(attachment.mime_type)) {
      const blobUrl = mediaBlobUrls[attachment.id];
      
      return (
        <div className="mt-2 mb-2">
          {blobUrl ? (
            <video controls className="max-w-sm max-h-64 rounded-lg">
              <source src={blobUrl} type={attachment.mime_type} />
              Your browser does not support the video element.
            </video>
          ) : (
            <div className="flex items-center space-x-2 text-gray-500 text-sm">
              <Video className="w-4 h-4 animate-pulse" />
              <span>Loading video...</span>
            </div>
          )}
        </div>
      );
    }

    return null;
  };

  return (
    <div className="mt-2 space-y-2">
      {attachments.map((attachment) => (
        <div key={attachment.id}>
          {/* Preview for media files */}
          {renderPreview(attachment)}
          
          {/* File info card */}
          <div className="flex items-center justify-between p-3 bg-gray-50 border border-gray-200 rounded-lg hover:bg-gray-100 transition-colors">
            <div className="flex items-center space-x-3 flex-1 min-w-0">
              {getFileIcon(attachment.mime_type)}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate">
                  {attachment.filename}
                </p>
                <p className="text-xs text-gray-500">
                  {formatFileSize(attachment.size)}
                </p>
              </div>
            </div>
            <div className="flex items-center space-x-1">
              <button
                onClick={() => handleDownload(attachment.id, attachment.filename)}
                className="p-2 hover:bg-gray-200 rounded-full transition-colors flex-shrink-0"
                title="Download file"
              >
                <Download className="w-4 h-4 text-gray-600" />
              </button>
              {isAdmin && (
                <button
                  onClick={() => handleDelete(attachment.id, attachment.filename)}
                  className="p-2 hover:bg-red-100 rounded-full transition-colors flex-shrink-0"
                  title="Delete attachment (Admin only)"
                >
                  <Trash2 className="w-4 h-4 text-red-600" />
                </button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

export default MessageAttachments;
