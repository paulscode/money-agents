import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { learningService } from '@/services/learning';
import type { CampaignLesson, LessonCategory } from '@/types';
import {
  Loader2,
  Search,
  Filter,
  BookOpen,
  XCircle,
  Clock,
  UserX,
  DollarSign,
  Timer,
  Wrench,
  AlertTriangle,
  AlertCircle,
  Info,
  ChevronDown,
  ChevronRight,
  Shield,
  ListChecks,
} from 'lucide-react';

const categoryConfig: Record<LessonCategory, { icon: React.ElementType; label: string; color: string; bgColor: string }> = {
  failure: { icon: XCircle, label: 'Failure', color: 'text-red-400', bgColor: 'bg-red-500/20' },
  inefficiency: { icon: Clock, label: 'Inefficiency', color: 'text-orange-400', bgColor: 'bg-orange-500/20' },
  user_friction: { icon: UserX, label: 'User Friction', color: 'text-yellow-400', bgColor: 'bg-yellow-500/20' },
  budget_issue: { icon: DollarSign, label: 'Budget Issue', color: 'text-pink-400', bgColor: 'bg-pink-500/20' },
  timing: { icon: Timer, label: 'Timing', color: 'text-purple-400', bgColor: 'bg-purple-500/20' },
  tool_issue: { icon: Wrench, label: 'Tool Issue', color: 'text-blue-400', bgColor: 'bg-blue-500/20' },
};

const severityConfig: Record<string, { icon: React.ElementType; label: string; color: string; bgColor: string }> = {
  critical: { icon: AlertTriangle, label: 'Critical', color: 'text-red-400', bgColor: 'bg-red-500/20 border-red-500/30' },
  high: { icon: AlertCircle, label: 'High', color: 'text-orange-400', bgColor: 'bg-orange-500/20 border-orange-500/30' },
  medium: { icon: AlertCircle, label: 'Medium', color: 'text-yellow-400', bgColor: 'bg-yellow-500/20 border-yellow-500/30' },
  low: { icon: Info, label: 'Low', color: 'text-blue-400', bgColor: 'bg-blue-500/20 border-blue-500/30' },
};

interface LessonCardProps {
  lesson: CampaignLesson;
  onSelect?: (lesson: CampaignLesson) => void;
}

function LessonCard({ lesson, onSelect }: LessonCardProps) {
  const [expanded, setExpanded] = useState(false);
  
  const catConfig = categoryConfig[lesson.category];
  const sevConfig = severityConfig[lesson.impact_severity];
  const CategoryIcon = catConfig.icon;
  const SeverityIcon = sevConfig.icon;
  
  return (
    <div className={`bg-gray-900/50 border rounded-lg overflow-hidden hover:border-gray-700 transition-colors ${
      lesson.impact_severity === 'critical' ? 'border-red-500/30' : 'border-gray-800'
    }`}>
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
        
        <div className={`p-2 rounded-lg ${catConfig.bgColor} ${catConfig.color}`}>
          <CategoryIcon className="h-4 w-4" />
        </div>
        
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-white truncate">
            {lesson.title}
          </h4>
          <p className="text-xs text-gray-500">{catConfig.label}</p>
        </div>
        
        <div className="flex items-center gap-2">
          {/* Severity Badge */}
          <span className={`px-2 py-0.5 rounded text-xs flex items-center gap-1 border ${sevConfig.bgColor}`}>
            <SeverityIcon className={`h-3 w-3 ${sevConfig.color}`} />
            <span className={sevConfig.color}>{sevConfig.label}</span>
          </span>
          
          {/* Times Applied */}
          {lesson.times_applied > 0 && (
            <span className="text-xs text-gray-500">
              Used {lesson.times_applied}x
            </span>
          )}
        </div>
      </button>
      
      {/* Expanded Content */}
      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-gray-800">
          <p className="text-sm text-gray-400 pt-3">{lesson.description}</p>
          
          {/* Trigger Event */}
          <div className="space-y-1">
            <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" />
              What Happened
            </h5>
            <p className="text-sm text-white bg-gray-800/50 rounded-lg p-2">
              {lesson.trigger_event}
            </p>
          </div>
          
          {/* Impact */}
          <div className="grid grid-cols-2 gap-3">
            {lesson.budget_impact && (
              <div className="p-2 bg-gray-800/50 rounded-lg">
                <p className="text-xs text-gray-500">Budget Impact</p>
                <p className="text-sm font-medium text-red-400">
                  -${lesson.budget_impact.toLocaleString()}
                </p>
              </div>
            )}
            {lesson.time_impact_minutes && (
              <div className="p-2 bg-gray-800/50 rounded-lg">
                <p className="text-xs text-gray-500">Time Lost</p>
                <p className="text-sm font-medium text-orange-400">
                  {lesson.time_impact_minutes} min
                </p>
              </div>
            )}
          </div>
          
          {/* Prevention Steps */}
          <div className="space-y-2">
            <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
              <Shield className="h-3 w-3" />
              Prevention Steps
            </h5>
            <ul className="space-y-1">
              {lesson.prevention_steps.map((step, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-neon-cyan mt-0.5">•</span>
                  <span className="text-gray-300">{step}</span>
                </li>
              ))}
            </ul>
          </div>
          
          {/* Detection Signals */}
          {lesson.detection_signals && lesson.detection_signals.length > 0 && (
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
                <ListChecks className="h-3 w-3" />
                Warning Signs
              </h5>
              <ul className="space-y-1">
                {lesson.detection_signals.map((signal, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="text-yellow-400 mt-0.5">⚠</span>
                    <span className="text-gray-400">{signal}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          
          {/* Tags */}
          {lesson.tags && lesson.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {lesson.tags.map((tag, i) => (
                <span key={i} className="px-2 py-0.5 bg-gray-800 text-gray-400 rounded text-xs">
                  {tag}
                </span>
              ))}
            </div>
          )}
          
          {/* Footer */}
          <div className="flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-800">
            <span>Learned: {new Date(lesson.created_at).toLocaleDateString()}</span>
          </div>
          
          {onSelect && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect(lesson);
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

interface LessonViewerProps {
  campaignId?: string;
  onSelectLesson?: (lesson: CampaignLesson) => void;
}

export function LessonViewer({ campaignId, onSelectLesson }: LessonViewerProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('');
  const [severityFilter, setSeverityFilter] = useState<string>('');
  const [showFilters, setShowFilters] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['lessons', { category: categoryFilter, severity: severityFilter, campaign_id: campaignId }],
    queryFn: () => learningService.listLessons({
      category: categoryFilter || undefined,
      severity: severityFilter || undefined,
      campaign_id: campaignId,
      limit: 50,
    }),
  });

  const filteredLessons = data?.lessons.filter(l => 
    !searchQuery || 
    l.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    l.description.toLowerCase().includes(searchQuery.toLowerCase())
  ) ?? [];

  // Group by severity for summary
  const criticalCount = filteredLessons.filter(l => l.impact_severity === 'critical').length;
  const highCount = filteredLessons.filter(l => l.impact_severity === 'high').length;

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
        <p>Failed to load lessons</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-neon-cyan" />
          Lessons Learned
        </h3>
        <div className="flex items-center gap-2 text-xs">
          {criticalCount > 0 && (
            <span className="px-2 py-1 bg-red-500/20 text-red-400 rounded-full">
              {criticalCount} Critical
            </span>
          )}
          {highCount > 0 && (
            <span className="px-2 py-1 bg-orange-500/20 text-orange-400 rounded-full">
              {highCount} High
            </span>
          )}
          <span className="text-gray-500">
            {filteredLessons.length} total
          </span>
        </div>
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
              placeholder="Search lessons..."
              className="w-full pl-10 pr-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm"
            />
          </div>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`px-3 py-2 rounded-lg border transition-colors flex items-center gap-2 ${
              showFilters || categoryFilter || severityFilter
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
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="px-3 py-1.5 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white focus:border-neon-cyan"
            >
              <option value="">All Categories</option>
              {Object.entries(categoryConfig).map(([key, config]) => (
                <option key={key} value={key}>{config.label}</option>
              ))}
            </select>
            
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="px-3 py-1.5 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white focus:border-neon-cyan"
            >
              <option value="">All Severities</option>
              {Object.entries(severityConfig).map(([key, config]) => (
                <option key={key} value={key}>{config.label}</option>
              ))}
            </select>
            
            {(categoryFilter || severityFilter) && (
              <button
                onClick={() => {
                  setCategoryFilter('');
                  setSeverityFilter('');
                }}
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white"
              >
                Clear filters
              </button>
            )}
          </div>
        )}
      </div>

      {/* Lesson List */}
      {filteredLessons.length > 0 ? (
        <div className="space-y-2">
          {filteredLessons.map((lesson) => (
            <LessonCard
              key={lesson.id}
              lesson={lesson}
              onSelect={onSelectLesson}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-gray-400">
          <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="text-lg mb-1">No lessons found</p>
          <p className="text-sm text-gray-500">
            {searchQuery || categoryFilter || severityFilter
              ? 'Try adjusting your filters'
              : 'Lessons are recorded when campaigns encounter issues'}
          </p>
        </div>
      )}
    </div>
  );
}
