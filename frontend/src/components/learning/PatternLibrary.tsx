import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { learningService } from '@/services/learning';
import type { CampaignPattern, PatternType, PatternStatus } from '@/types';
import {
  Loader2,
  Search,
  Filter,
  Zap,
  Clock,
  Wrench,
  Shield,
  TrendingUp,
  Timer,
  CheckCircle2,
  AlertTriangle,
  FlaskConical,
  ChevronDown,
  ChevronRight,
  Star,
} from 'lucide-react';

const patternTypeConfig: Record<PatternType, { icon: React.ElementType; label: string; color: string }> = {
  execution_sequence: { icon: Zap, label: 'Execution Sequence', color: 'text-neon-cyan' },
  input_collection: { icon: Clock, label: 'Input Collection', color: 'text-blue-400' },
  tool_combination: { icon: Wrench, label: 'Tool Combination', color: 'text-purple-400' },
  error_recovery: { icon: Shield, label: 'Error Recovery', color: 'text-orange-400' },
  optimization: { icon: TrendingUp, label: 'Optimization', color: 'text-green-400' },
  timing: { icon: Timer, label: 'Timing', color: 'text-yellow-400' },
};

const statusConfig: Record<PatternStatus, { icon: React.ElementType; label: string; bgColor: string }> = {
  active: { icon: CheckCircle2, label: 'Active', bgColor: 'bg-green-500/20 text-green-400' },
  deprecated: { icon: AlertTriangle, label: 'Deprecated', bgColor: 'bg-red-500/20 text-red-400' },
  experimental: { icon: FlaskConical, label: 'Experimental', bgColor: 'bg-yellow-500/20 text-yellow-400' },
};

interface PatternCardProps {
  pattern: CampaignPattern;
  onSelect?: (pattern: CampaignPattern) => void;
}

function PatternCard({ pattern, onSelect }: PatternCardProps) {
  const [expanded, setExpanded] = useState(false);
  
  const typeConfig = patternTypeConfig[pattern.pattern_type];
  const statusCfg = statusConfig[pattern.status];
  const TypeIcon = typeConfig.icon;
  const StatusIcon = statusCfg.icon;
  
  const confidencePercent = Math.round(pattern.confidence_score * 100);
  const successPercent = Math.round(pattern.success_rate * 100);
  
  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-700 transition-colors">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-800/30 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-500 flex-shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-500 flex-shrink-0" />
        )}
        
        <div className={`p-2 rounded-lg bg-gray-800/50 ${typeConfig.color}`}>
          <TypeIcon className="h-4 w-4" />
        </div>
        
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-white truncate">
            {pattern.name}
          </h4>
          <p className="text-xs text-gray-500">{typeConfig.label}</p>
        </div>
        
        <div className="flex items-center gap-2">
          {/* Confidence Badge */}
          <div className="flex items-center gap-1 text-xs">
            <Star className={`h-3 w-3 ${confidencePercent >= 70 ? 'text-yellow-400' : 'text-gray-500'}`} />
            <span className="text-gray-400">{confidencePercent}%</span>
          </div>
          
          {/* Status Badge */}
          <span className={`px-2 py-0.5 rounded text-xs flex items-center gap-1 ${statusCfg.bgColor}`}>
            <StatusIcon className="h-3 w-3" />
            {statusCfg.label}
          </span>
        </div>
      </button>
      
      {/* Expanded Content */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-gray-800">
          <p className="text-sm text-gray-400 pt-3">{pattern.description}</p>
          
          {/* Statistics */}
          <div className="grid grid-cols-3 gap-3">
            <div className="p-2 bg-gray-800/50 rounded-lg text-center">
              <p className="text-lg font-bold text-white">{pattern.times_applied}</p>
              <p className="text-xs text-gray-500">Times Applied</p>
            </div>
            <div className="p-2 bg-gray-800/50 rounded-lg text-center">
              <p className="text-lg font-bold text-green-400">{pattern.times_successful}</p>
              <p className="text-xs text-gray-500">Successful</p>
            </div>
            <div className="p-2 bg-gray-800/50 rounded-lg text-center">
              <p className="text-lg font-bold text-neon-cyan">{successPercent}%</p>
              <p className="text-xs text-gray-500">Success Rate</p>
            </div>
          </div>
          
          {/* Pattern Data Preview */}
          {pattern.pattern_data && (
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-gray-400 uppercase">Pattern Details</h5>
              <div className="p-2 bg-gray-900/70 rounded-lg">
                <pre className="text-xs text-gray-400 overflow-x-auto">
                  {JSON.stringify(pattern.pattern_data, null, 2).slice(0, 500)}
                  {JSON.stringify(pattern.pattern_data).length > 500 && '...'}
                </pre>
              </div>
            </div>
          )}
          
          {/* Tags */}
          {pattern.tags && pattern.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {pattern.tags.map((tag, i) => (
                <span key={i} className="px-2 py-0.5 bg-gray-800 text-gray-400 rounded text-xs">
                  {tag}
                </span>
              ))}
            </div>
          )}
          
          {/* Footer */}
          <div className="flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-800">
            <span>Created: {new Date(pattern.created_at).toLocaleDateString()}</span>
            {pattern.last_applied_at && (
              <span>Last used: {new Date(pattern.last_applied_at).toLocaleDateString()}</span>
            )}
          </div>
          
          {onSelect && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect(pattern);
              }}
              className="w-full btn-secondary text-sm"
            >
              View Full Details
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface PatternLibraryProps {
  campaignId?: string;
  onSelectPattern?: (pattern: CampaignPattern) => void;
}

export function PatternLibrary({ onSelectPattern }: PatternLibraryProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [showFilters, setShowFilters] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['patterns', { type: typeFilter, status: statusFilter }],
    queryFn: () => learningService.listPatterns({
      pattern_type: typeFilter || undefined,
      status_filter: statusFilter || undefined,
      include_global: true,
      limit: 50,
    }),
  });

  const filteredPatterns = data?.patterns.filter(p => 
    !searchQuery || 
    p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    p.description.toLowerCase().includes(searchQuery.toLowerCase())
  ) ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-neon-cyan" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8 text-red-400">
        <AlertTriangle className="h-8 w-8 mx-auto mb-2" />
        <p>Failed to load patterns</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <Zap className="h-5 w-5 text-neon-cyan" />
          Pattern Library
        </h3>
        <span className="text-sm text-gray-500">
          {filteredPatterns.length} pattern{filteredPatterns.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Search & Filters */}
      <div className="space-y-2">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search patterns..."
              className="w-full pl-10 pr-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm"
            />
          </div>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`px-3 py-2 rounded-lg border transition-colors flex items-center gap-2 ${
              showFilters || typeFilter || statusFilter
                ? 'border-neon-cyan bg-neon-cyan/10 text-neon-cyan'
                : 'border-gray-700 text-gray-400 hover:border-gray-600'
            }`}
          >
            <Filter className="h-4 w-4" />
          </button>
        </div>

        {showFilters && (
          <div className="flex gap-2 flex-wrap">
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-1.5 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white focus:border-neon-cyan"
            >
              <option value="">All Types</option>
              {Object.entries(patternTypeConfig).map(([key, config]) => (
                <option key={key} value={key}>{config.label}</option>
              ))}
            </select>
            
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-1.5 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white focus:border-neon-cyan"
            >
              <option value="">All Statuses</option>
              {Object.entries(statusConfig).map(([key, config]) => (
                <option key={key} value={key}>{config.label}</option>
              ))}
            </select>
            
            {(typeFilter || statusFilter) && (
              <button
                onClick={() => {
                  setTypeFilter('');
                  setStatusFilter('');
                }}
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white"
              >
                Clear filters
              </button>
            )}
          </div>
        )}
      </div>

      {/* Pattern List */}
      {filteredPatterns.length > 0 ? (
        <div className="space-y-2">
          {filteredPatterns.map((pattern) => (
            <PatternCard
              key={pattern.id}
              pattern={pattern}
              onSelect={onSelectPattern}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-gray-400">
          <Zap className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="text-lg mb-1">No patterns found</p>
          <p className="text-sm text-gray-500">
            {searchQuery || typeFilter || statusFilter
              ? 'Try adjusting your filters'
              : 'Patterns are discovered from completed campaigns'}
          </p>
        </div>
      )}
    </div>
  );
}
