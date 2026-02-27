/**
 * NotificationBell - Shows notification count badge and dropdown panel.
 */
import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bell, X, Check, CheckCheck, ExternalLink, AlertTriangle, Info, AlertCircle, Zap } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import {
  getNotifications,
  getNotificationCounts,
  markNotificationRead,
  markAllNotificationsRead,
  dismissNotification,
} from '@/services/notifications';
import type { Notification, NotificationPriority } from '@/types/notification';

/**
 * Get icon for notification priority.
 */
function getPriorityIcon(priority: NotificationPriority) {
  switch (priority) {
    case 'urgent':
      return <AlertTriangle className="w-4 h-4 text-red-400" />;
    case 'high':
      return <AlertCircle className="w-4 h-4 text-orange-400" />;
    case 'medium':
      return <Zap className="w-4 h-4 text-yellow-400" />;
    default:
      return <Info className="w-4 h-4 text-blue-400" />;
  }
}

/**
 * Get background color class for notification priority.
 */
function getPriorityBgClass(priority: NotificationPriority, isRead: boolean) {
  if (isRead) return 'bg-navy-900/50';
  switch (priority) {
    case 'urgent':
      return 'bg-red-900/20 border-l-2 border-red-500';
    case 'high':
      return 'bg-orange-900/20 border-l-2 border-orange-500';
    case 'medium':
      return 'bg-yellow-900/10 border-l-2 border-yellow-500';
    default:
      return 'bg-navy-800/50 border-l-2 border-navy-600';
  }
}

interface NotificationItemProps {
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDismiss: (id: string) => void;
  onNavigate: (link: string) => void;
}

function NotificationItem({ notification, onMarkRead, onDismiss, onNavigate }: NotificationItemProps) {
  const handleClick = () => {
    if (!notification.is_read) {
      onMarkRead(notification.id);
    }
    if (notification.link) {
      onNavigate(notification.link);
    }
  };

  return (
    <div
      className={`p-3 ${getPriorityBgClass(notification.priority, notification.is_read)} hover:bg-navy-700/50 cursor-pointer transition-colors`}
      onClick={handleClick}
    >
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 mt-0.5">
          {getPriorityIcon(notification.priority)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <h4 className={`text-sm font-medium truncate ${notification.is_read ? 'text-gray-400' : 'text-white'}`}>
              {notification.title}
            </h4>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDismiss(notification.id);
              }}
              className="flex-shrink-0 p-1 hover:bg-navy-600 rounded text-gray-500 hover:text-gray-300"
              title="Dismiss"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
          <p className={`text-xs mt-1 line-clamp-2 ${notification.is_read ? 'text-gray-500' : 'text-gray-400'}`}>
            {notification.message}
          </p>
          <div className="flex items-center justify-between mt-2">
            <span className="text-xs text-gray-500">
              {formatDistanceToNow(new Date(notification.created_at), { addSuffix: true })}
            </span>
            {notification.link && notification.link_text && (
              <span className="text-xs text-neon-cyan flex items-center gap-1">
                {notification.link_text}
                <ExternalLink className="w-3 h-3" />
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function NotificationBell() {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Get notification counts for badge
  const { data: counts } = useQuery({
    queryKey: ['notification-counts'],
    queryFn: getNotificationCounts,
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  // Get notifications when dropdown is open
  const { data: notificationsData, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => getNotifications({ limit: 20 }),
    enabled: isOpen,
  });

  // Mark as read mutation
  const markReadMutation = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
      queryClient.invalidateQueries({ queryKey: ['notification-counts'] });
    },
  });

  // Mark all as read mutation
  const markAllReadMutation = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
      queryClient.invalidateQueries({ queryKey: ['notification-counts'] });
    },
  });

  // Dismiss mutation
  const dismissMutation = useMutation({
    mutationFn: dismissNotification,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
      queryClient.invalidateQueries({ queryKey: ['notification-counts'] });
    },
  });

  // Handle click outside to close dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [isOpen]);

  // Handle navigation — only allow relative paths to prevent open redirect
  // Reject protocol-relative URLs (//evil.com) which bypass same-origin (RT-46)
  const handleNavigate = (link: string) => {
    setIsOpen(false);
    if (link.startsWith('/') && !link.startsWith('//')) {
      window.location.href = link;
    }
  };

  const totalUnread = counts?.total || 0;
  const hasUrgent = (counts?.by_priority?.urgent || 0) > 0;
  const hasHigh = (counts?.by_priority?.high || 0) > 0;

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Bell Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`relative p-2 rounded-lg transition-colors ${
          isOpen ? 'bg-navy-700' : 'hover:bg-navy-800'
        }`}
        title={`${totalUnread} unread notifications`}
      >
        <Bell className={`w-5 h-5 ${hasUrgent ? 'text-red-400' : hasHigh ? 'text-orange-400' : 'text-gray-400'}`} />
        
        {/* Badge */}
        {totalUnread > 0 && (
          <span
            className={`absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center text-xs font-bold rounded-full ${
              hasUrgent ? 'bg-red-500' : hasHigh ? 'bg-orange-500' : 'bg-neon-cyan'
            } text-white`}
          >
            {totalUnread > 99 ? '99+' : totalUnread}
          </span>
        )}
      </button>

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-96 max-h-[70vh] bg-navy-900 border border-navy-700 rounded-lg shadow-xl overflow-hidden z-50">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-navy-700 bg-navy-800">
            <h3 className="text-sm font-semibold text-white">Notifications</h3>
            {totalUnread > 0 && (
              <button
                onClick={() => markAllReadMutation.mutate()}
                className="text-xs text-neon-cyan hover:text-neon-cyan/80 flex items-center gap-1"
                disabled={markAllReadMutation.isPending}
              >
                <CheckCheck className="w-3 h-3" />
                Mark all read
              </button>
            )}
          </div>

          {/* Notification List */}
          <div className="overflow-y-auto max-h-[calc(70vh-60px)]">
            {isLoading ? (
              <div className="p-8 text-center text-gray-500">
                Loading...
              </div>
            ) : notificationsData?.notifications.length === 0 ? (
              <div className="p-8 text-center">
                <Bell className="w-12 h-12 mx-auto text-gray-600 mb-3" />
                <p className="text-gray-500">No notifications</p>
              </div>
            ) : (
              <div className="divide-y divide-navy-700/50">
                {notificationsData?.notifications.map((notification) => (
                  <NotificationItem
                    key={notification.id}
                    notification={notification}
                    onMarkRead={(id) => markReadMutation.mutate(id)}
                    onDismiss={(id) => dismissMutation.mutate(id)}
                    onNavigate={handleNavigate}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          {(notificationsData?.notifications.length || 0) > 0 && (
            <div className="px-4 py-2 border-t border-navy-700 bg-navy-800">
              <button
                onClick={() => {
                  setIsOpen(false);
                  window.location.href = '/notifications';
                }}
                className="text-xs text-gray-400 hover:text-white w-full text-center"
              >
                View all notifications
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
