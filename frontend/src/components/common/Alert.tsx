import { AlertCircle, CheckCircle, Info, XCircle, X } from 'lucide-react';
import { useEffect } from 'react';

export type AlertType = 'success' | 'error' | 'warning' | 'info';

interface AlertProps {
  type: AlertType;
  title: string;
  message?: string;
  onClose: () => void;
  autoClose?: boolean;
  autoCloseDuration?: number;
}

export function Alert({ 
  type, 
  title, 
  message, 
  onClose, 
  autoClose = false, 
  autoCloseDuration = 5000 
}: AlertProps) {
  useEffect(() => {
    if (autoClose) {
      const timer = setTimeout(() => {
        onClose();
      }, autoCloseDuration);
      return () => clearTimeout(timer);
    }
  }, [autoClose, autoCloseDuration, onClose]);

  const styles = {
    success: {
      bg: 'bg-green-900/20',
      border: 'border-green-500/50',
      icon: 'text-green-500',
      IconComponent: CheckCircle,
    },
    error: {
      bg: 'bg-red-900/20',
      border: 'border-red-500/50',
      icon: 'text-red-500',
      IconComponent: XCircle,
    },
    warning: {
      bg: 'bg-yellow-900/20',
      border: 'border-yellow-500/50',
      icon: 'text-yellow-500',
      IconComponent: AlertCircle,
    },
    info: {
      bg: 'bg-blue-900/20',
      border: 'border-blue-500/50',
      icon: 'text-blue-500',
      IconComponent: Info,
    },
  };

  const style = styles[type];
  const Icon = style.IconComponent;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 pointer-events-none">
      <div 
        className={`${style.bg} ${style.border} border-2 rounded-lg p-4 shadow-2xl max-w-md w-full pointer-events-auto animate-in slide-in-from-top-5 duration-300`}
        role="alert"
      >
        <div className="flex items-start gap-3">
          <Icon className={`h-6 w-6 ${style.icon} flex-shrink-0 mt-0.5`} />
          <div className="flex-1 min-w-0">
            <h3 className="text-white font-semibold text-lg mb-1">{title}</h3>
            {message && (
              <p className="text-gray-300 text-sm leading-relaxed">{message}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors flex-shrink-0"
            aria-label="Close alert"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      </div>
    </div>
  );
}
