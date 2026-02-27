import { Check, X, RotateCcw, AlertTriangle } from 'lucide-react';

interface BulkActionsToolbarProps {
  selectedCount: number;
  totalCount: number;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onBulkApprove: () => void;
  onBulkDismiss: () => void;
  isApproving?: boolean;
  isDismissing?: boolean;
}

export function BulkActionsToolbar({
  selectedCount,
  totalCount,
  onSelectAll,
  onDeselectAll,
  onBulkApprove,
  onBulkDismiss,
  isApproving,
  isDismissing,
}: BulkActionsToolbarProps) {
  const isAllSelected = selectedCount === totalCount && totalCount > 0;
  const hasSelection = selectedCount > 0;
  const isLoading = isApproving || isDismissing;

  return (
    <div className="flex items-center justify-between bg-gray-800/50 border border-gray-700 rounded-lg px-4 py-2">
      <div className="flex items-center gap-4">
        {/* Selection toggle */}
        <button
          onClick={isAllSelected ? onDeselectAll : onSelectAll}
          className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors"
        >
          <div
            className={`w-4 h-4 rounded border flex items-center justify-center ${
              isAllSelected
                ? 'bg-neon-cyan border-neon-cyan'
                : selectedCount > 0
                ? 'bg-neon-cyan/50 border-neon-cyan'
                : 'border-gray-600'
            }`}
          >
            {isAllSelected && <Check className="h-3 w-3 text-gray-900" />}
            {selectedCount > 0 && !isAllSelected && (
              <div className="w-1.5 h-1.5 bg-gray-900 rounded-sm" />
            )}
          </div>
          <span>
            {isAllSelected
              ? 'Deselect All'
              : selectedCount > 0
              ? `${selectedCount} selected`
              : 'Select All'}
          </span>
        </button>

        {/* Selection count */}
        {hasSelection && (
          <span className="text-sm text-gray-500">
            {selectedCount} of {totalCount}
          </span>
        )}
      </div>

      {/* Bulk actions */}
      {hasSelection && (
        <div className="flex items-center gap-2">
          {/* Reset selection */}
          <button
            onClick={onDeselectAll}
            className="px-3 py-1.5 text-sm text-gray-400 hover:text-white transition-colors flex items-center gap-1"
            title="Clear selection"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Clear
          </button>

          {/* Bulk approve */}
          <button
            onClick={onBulkApprove}
            disabled={isLoading}
            className="px-3 py-1.5 text-sm bg-green-500/20 text-green-400 hover:bg-green-500/30 rounded-lg transition-colors flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Check className="h-3.5 w-3.5" />
            Approve {selectedCount}
          </button>

          {/* Bulk dismiss */}
          <button
            onClick={onBulkDismiss}
            disabled={isLoading}
            className="px-3 py-1.5 text-sm bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded-lg transition-colors flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <X className="h-3.5 w-3.5" />
            Dismiss {selectedCount}
          </button>
        </div>
      )}

      {/* Warning for large selection */}
      {selectedCount >= 10 && (
        <div className="flex items-center gap-1 text-xs text-yellow-400">
          <AlertTriangle className="h-3 w-3" />
          Large selection
        </div>
      )}
    </div>
  );
}
