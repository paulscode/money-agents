/**
 * Campaign Intelligence Panel
 * 
 * Displays patterns, lessons learned, and effectiveness trends
 * for campaigns. Part of Phase C Analytics improvements.
 * 
 * Features:
 * - Top patterns with "Apply to New Proposal" action
 * - Recent lessons sorted by severity
 * - Effectiveness trend chart
 * - Summary statistics
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Brain,
  Lightbulb,
  AlertTriangle,
  TrendingUp,
  ChevronDown,
  ChevronUp,
  Sparkles,
  BookOpen,
  Target,
  CheckCircle,
  XCircle,
  ArrowRight,
  Info,
} from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import analyticsService from '@/services/analytics';
import type {
  PatternSummary,
  LessonSummary,
} from '@/services/analytics';

// =============================================================================
// Sub-components
// =============================================================================

interface StatCardProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number;
  color: string;
  subtitle?: string;
}

function StatCard({ icon: Icon, label, value, color, subtitle }: StatCardProps) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`h-4 w-4 ${color}`} />
        <span className="text-xs text-gray-400">{label}</span>
      </div>
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      {subtitle && <div className="text-xs text-gray-500 mt-0.5">{subtitle}</div>}
    </div>
  );
}

interface PatternCardProps {
  pattern: PatternSummary;
  onApply: (patternId: string) => void;
  isApplying: boolean;
}

function PatternCard({ pattern, onApply, isApplying }: PatternCardProps) {
  const confidenceColor = pattern.confidence_score >= 0.8 
    ? 'text-green-400' 
    : pattern.confidence_score >= 0.6 
      ? 'text-amber-400' 
      : 'text-gray-400';
  
  const successColor = pattern.success_rate >= 80 
    ? 'text-green-400' 
    : pattern.success_rate >= 60 
      ? 'text-amber-400' 
      : 'text-red-400';

  return (
    <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700 hover:border-gray-600 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <div className="flex-1">
          <h4 className="text-sm font-medium text-white flex items-center gap-2">
            {pattern.name}
            {pattern.is_global && (
              <span className="px-1.5 py-0.5 text-xs bg-neon-purple/20 text-neon-purple rounded">
                Global
              </span>
            )}
          </h4>
          <p className="text-xs text-gray-500 mt-0.5">
            {pattern.pattern_type} • {pattern.agent_type || 'Any agent'}
          </p>
        </div>
        <button
          onClick={() => onApply(pattern.id)}
          disabled={isApplying}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-neon-cyan/20 text-neon-cyan rounded-md hover:bg-neon-cyan/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isApplying ? (
            <span className="animate-pulse">Creating...</span>
          ) : (
            <>
              <Sparkles className="h-3.5 w-3.5" />
              Apply
            </>
          )}
        </button>
      </div>
      
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <span className="text-gray-500">Confidence</span>
          <div className={`font-medium ${confidenceColor}`}>
            {(pattern.confidence_score * 100).toFixed(0)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Success Rate</span>
          <div className={`font-medium ${successColor}`}>
            {pattern.success_rate.toFixed(0)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Used</span>
          <div className="font-medium text-gray-300">{pattern.times_used}x</div>
        </div>
      </div>
      
      {pattern.average_yield !== null && (
        <div className="mt-2 pt-2 border-t border-gray-700/50 text-xs">
          <span className="text-gray-500">Avg Yield: </span>
          <span className="text-green-400">${pattern.average_yield.toFixed(2)}</span>
          {pattern.target_market && (
            <span className="text-gray-500 ml-2">• {pattern.target_market}</span>
          )}
        </div>
      )}
    </div>
  );
}

interface LessonCardProps {
  lesson: LessonSummary;
}

function LessonCard({ lesson }: LessonCardProps) {
  const [expanded, setExpanded] = useState(false);
  
  const severityConfig = {
    critical: { color: 'text-red-400', bg: 'bg-red-500/20', icon: XCircle },
    high: { color: 'text-orange-400', bg: 'bg-orange-500/20', icon: AlertTriangle },
    medium: { color: 'text-amber-400', bg: 'bg-amber-500/20', icon: Info },
    low: { color: 'text-gray-400', bg: 'bg-gray-500/20', icon: Info },
  };
  
  const config = severityConfig[lesson.severity as keyof typeof severityConfig] || severityConfig.low;
  const Icon = config.icon;

  return (
    <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left"
      >
        <div className="flex items-start gap-2">
          <span className={`mt-0.5 px-1.5 py-0.5 text-xs font-medium ${config.bg} ${config.color} rounded flex items-center gap-1`}>
            <Icon className="h-3 w-3" />
            {lesson.severity}
          </span>
          <div className="flex-1">
            <h4 className="text-sm font-medium text-white">{lesson.title}</h4>
            <p className="text-xs text-gray-500 mt-0.5">
              {lesson.category}
              {lesson.pattern_name && ` • ${lesson.pattern_name}`}
            </p>
          </div>
          {lesson.prevention_steps && lesson.prevention_steps.length > 0 && (
            expanded ? (
              <ChevronUp className="h-4 w-4 text-gray-400" />
            ) : (
              <ChevronDown className="h-4 w-4 text-gray-400" />
            )
          )}
        </div>
      </button>
      
      {expanded && lesson.prevention_steps && lesson.prevention_steps.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-700/50">
          <h5 className="text-xs font-medium text-gray-400 mb-2">Prevention Steps:</h5>
          <ul className="space-y-1">
            {lesson.prevention_steps.map((step, idx) => (
              <li key={idx} className="text-xs text-gray-300 flex items-start gap-2">
                <span className="text-neon-cyan">•</span>
                {step}
              </li>
            ))}
          </ul>
          {lesson.failure_analysis && (
            <div className="mt-2 p-2 bg-gray-900/50 rounded text-xs text-gray-400">
              <span className="font-medium">Analysis:</span> {lesson.failure_analysis}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export function CampaignIntelligencePanel() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(true);
  const [activeTab, setActiveTab] = useState<'patterns' | 'lessons'>('patterns');

  // Fetch patterns
  const { data: patterns, isLoading: patternsLoading } = useQuery({
    queryKey: ['campaignPatterns'],
    queryFn: () => analyticsService.getTopPatterns(10, 0.3),
    refetchInterval: 300000, // 5 minutes
  });

  // Fetch lessons
  const { data: lessons, isLoading: lessonsLoading } = useQuery({
    queryKey: ['campaignLessons'],
    queryFn: () => analyticsService.getRecentLessons(30, 10),
    refetchInterval: 300000,
  });

  // Fetch effectiveness trend
  const { data: trend, isLoading: trendLoading } = useQuery({
    queryKey: ['campaignEffectiveness'],
    queryFn: () => analyticsService.getEffectivenessTrend(30),
    refetchInterval: 300000,
  });

  // Fetch summary
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['campaignSummary'],
    queryFn: () => analyticsService.getIntelligenceSummary(),
    refetchInterval: 60000, // 1 minute
  });

  // Apply pattern mutation
  const applyPatternMutation = useMutation({
    mutationFn: (patternId: string) => analyticsService.createProposalFromPattern(patternId),
    onSuccess: (data: unknown) => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
      // Navigate to the new proposal
      if (data && typeof data === 'object' && 'id' in data) {
        navigate(`/proposals/${(data as { id: string }).id}`);
      } else {
        navigate('/proposals');
      }
    },
  });

  const isLoading = patternsLoading || lessonsLoading || trendLoading || summaryLoading;

  // Format chart data
  const chartData = trend?.map(t => ({
    week: new Date(t.week).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    'Success Rate': t.success_rate,
    campaigns: t.campaign_count,
  })) || [];

  // Count critical/high lessons
  const criticalLessons = lessons?.filter(l => l.severity === 'critical' || l.severity === 'high').length || 0;

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-gray-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <Brain className="h-5 w-5 text-neon-purple" />
          <h3 className="text-lg font-semibold text-white">Campaign Intelligence</h3>
          {criticalLessons > 0 && (
            <span className="px-2 py-0.5 text-xs font-medium bg-red-500/20 text-red-400 rounded-full">
              {criticalLessons} critical
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronUp className="h-5 w-5 text-gray-400" />
        ) : (
          <ChevronDown className="h-5 w-5 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="p-4 pt-0 space-y-6">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-neon-cyan"></div>
            </div>
          ) : (
            <>
              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard
                  icon={Target}
                  label="Active Patterns"
                  value={summary?.active_patterns || 0}
                  color="text-neon-purple"
                  subtitle={`${summary?.total_patterns || 0} total`}
                />
                <StatCard
                  icon={CheckCircle}
                  label="Campaign Success"
                  value={`${(summary?.campaign_success_rate || 0).toFixed(0)}%`}
                  color={(summary?.campaign_success_rate || 0) >= 70 ? 'text-green-400' : 'text-amber-400'}
                  subtitle={`${summary?.successful_campaigns || 0}/${summary?.recent_campaigns || 0} recent`}
                />
                <StatCard
                  icon={Sparkles}
                  label="Avg Confidence"
                  value={`${((summary?.average_pattern_confidence || 0) * 100).toFixed(0)}%`}
                  color="text-neon-cyan"
                />
                <StatCard
                  icon={BookOpen}
                  label="Lessons Learned"
                  value={summary?.total_lessons || 0}
                  color="text-amber-400"
                  subtitle={`${summary?.critical_lessons || 0} critical`}
                />
              </div>

              {/* Effectiveness Chart */}
              {chartData.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                    <TrendingUp className="h-4 w-4" />
                    Weekly Success Rate
                  </h4>
                  <div className="h-48 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                        <XAxis 
                          dataKey="week" 
                          tick={{ fill: '#9ca3af', fontSize: 11 }}
                          axisLine={{ stroke: '#374151' }}
                        />
                        <YAxis 
                          tick={{ fill: '#9ca3af', fontSize: 11 }}
                          axisLine={{ stroke: '#374151' }}
                          domain={[0, 100]}
                          unit="%"
                        />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1f2937',
                            border: '1px solid #374151',
                            borderRadius: '8px',
                          }}
                          labelStyle={{ color: '#fff' }}
                        />
                        <Line 
                          type="monotone" 
                          dataKey="Success Rate" 
                          stroke="#22d3ee" 
                          strokeWidth={2}
                          dot={{ fill: '#22d3ee', r: 4 }}
                          activeDot={{ r: 6 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Tabs */}
              <div className="border-b border-gray-700">
                <div className="flex gap-4">
                  <button
                    onClick={() => setActiveTab('patterns')}
                    className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
                      activeTab === 'patterns'
                        ? 'text-neon-cyan border-neon-cyan'
                        : 'text-gray-400 border-transparent hover:text-white'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <Lightbulb className="h-4 w-4" />
                      Top Patterns ({patterns?.length || 0})
                    </span>
                  </button>
                  <button
                    onClick={() => setActiveTab('lessons')}
                    className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
                      activeTab === 'lessons'
                        ? 'text-neon-cyan border-neon-cyan'
                        : 'text-gray-400 border-transparent hover:text-white'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <BookOpen className="h-4 w-4" />
                      Lessons Learned ({lessons?.length || 0})
                      {criticalLessons > 0 && (
                        <span className="w-2 h-2 rounded-full bg-red-500"></span>
                      )}
                    </span>
                  </button>
                </div>
              </div>

              {/* Tab Content */}
              {activeTab === 'patterns' && (
                <div className="space-y-3">
                  {patterns && patterns.length > 0 ? (
                    patterns.map((pattern) => (
                      <PatternCard
                        key={pattern.id}
                        pattern={pattern}
                        onApply={(id) => applyPatternMutation.mutate(id)}
                        isApplying={applyPatternMutation.isPending}
                      />
                    ))
                  ) : (
                    <div className="text-center py-6 text-gray-500">
                      <Lightbulb className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p>No patterns discovered yet</p>
                      <p className="text-xs mt-1">Run more campaigns to build intelligence</p>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'lessons' && (
                <div className="space-y-3">
                  {lessons && lessons.length > 0 ? (
                    lessons.map((lesson) => (
                      <LessonCard key={lesson.id} lesson={lesson} />
                    ))
                  ) : (
                    <div className="text-center py-6 text-gray-500">
                      <BookOpen className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p>No lessons recorded yet</p>
                      <p className="text-xs mt-1">Lessons are created from campaign outcomes</p>
                    </div>
                  )}
                </div>
              )}

              {/* View All Link */}
              <div className="flex justify-end pt-2">
                <button
                  onClick={() => navigate('/campaigns')}
                  className="text-sm text-gray-400 hover:text-neon-cyan flex items-center gap-1 transition-colors"
                >
                  View All Campaigns
                  <ArrowRight className="h-4 w-4" />
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default CampaignIntelligencePanel;
