import { useQuery } from '@tanstack/react-query';
import { resourcesService } from '@/services/resources';
import { Loader2, X, Cpu, HardDrive, Zap, Database, Box } from 'lucide-react';

interface ResourceSelectorProps {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  label?: string;
  helpText?: string;
}

const RESOURCE_TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  gpu: Zap,
  cpu: Cpu,
  ram: Database,
  storage: HardDrive,
  custom: Box,
};

const RESOURCE_TYPE_COLORS: Record<string, string> = {
  gpu: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  cpu: 'text-blue-400 bg-blue-500/10 border-blue-500/30',
  ram: 'text-purple-400 bg-purple-500/10 border-purple-500/30',
  storage: 'text-green-400 bg-green-500/10 border-green-500/30',
  custom: 'text-gray-400 bg-gray-500/10 border-gray-500/30',
};

export function ResourceSelector({
  selectedIds,
  onChange,
  label = 'Required Resources',
  helpText = 'Select resources this tool requires to operate',
}: ResourceSelectorProps) {
  const { data: resources = [], isLoading } = useQuery({
    queryKey: ['resources'],
    queryFn: () => resourcesService.getAll(),
  });

  // Show all resources except maintenance (which indicates hardware issues)
  const availableResources = resources.filter(
    (r) => r.status !== 'maintenance'
  );

  const handleToggle = (resourceId: string) => {
    if (selectedIds.includes(resourceId)) {
      onChange(selectedIds.filter((id) => id !== resourceId));
    } else {
      onChange([...selectedIds, resourceId]);
    }
  };

  const handleRemove = (resourceId: string) => {
    onChange(selectedIds.filter((id) => id !== resourceId));
  };

  const selectedResources = availableResources.filter((r) => selectedIds.includes(r.id));

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">Loading resources...</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <label className="block text-sm font-medium text-gray-300">
        {label}
      </label>

      {/* Selected resources */}
      {selectedResources.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selectedResources.map((resource) => {
            const Icon = RESOURCE_TYPE_ICONS[resource.resource_type] || Box;
            const colorClasses = RESOURCE_TYPE_COLORS[resource.resource_type] || RESOURCE_TYPE_COLORS.custom;
            return (
              <span
                key={resource.id}
                className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border ${colorClasses}`}
              >
                <Icon className="h-4 w-4" />
                <span className="text-sm">{resource.name}</span>
                <button
                  type="button"
                  onClick={() => handleRemove(resource.id)}
                  className="hover:opacity-70 transition-opacity"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </span>
            );
          })}
        </div>
      )}

      {/* Available resources to select */}
      {availableResources.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {availableResources.map((resource) => {
            const isSelected = selectedIds.includes(resource.id);
            const Icon = RESOURCE_TYPE_ICONS[resource.resource_type] || Box;
            const colorClasses = RESOURCE_TYPE_COLORS[resource.resource_type] || RESOURCE_TYPE_COLORS.custom;

            return (
              <button
                key={resource.id}
                type="button"
                onClick={() => handleToggle(resource.id)}
                className={`flex items-center gap-3 p-3 rounded-lg border transition-all text-left ${
                  isSelected
                    ? `${colorClasses} ring-1 ring-current`
                    : 'bg-gray-900/50 border-gray-700 hover:border-gray-600 text-gray-300'
                }`}
              >
                <Icon className={`h-5 w-5 ${isSelected ? '' : 'text-gray-500'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{resource.name}</div>
                  <div className="text-xs text-gray-500 capitalize">
                    {resource.resource_type}
                    {resource.category === 'capacity' && resource.total_bytes && (
                      <span className="ml-1">
                        ({formatBytes(resource.available_bytes || 0)} free)
                      </span>
                    )}
                  </div>
                </div>
                {isSelected && (
                  <div className="h-2 w-2 rounded-full bg-current" />
                )}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-gray-500 italic">
          No resources available. Go to Resources page to detect or add resources.
        </p>
      )}

      {helpText && (
        <p className="text-xs text-gray-500">{helpText}</p>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}
