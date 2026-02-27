import type { UserInputRequest, InputProvideRequest } from '@/types';
import { 
  Key, 
  FileText, 
  CheckSquare, 
  List, 
  Hash,
  Link,
  AlertTriangle,
  ChevronUp,
  ChevronDown,
  Loader2,
  Send,
  Zap
} from 'lucide-react';
import { useState, useMemo } from 'react';

interface InputRequestPanelProps {
  inputs: UserInputRequest[];
  onSubmit: (input: InputProvideRequest) => Promise<void>;
  onSubmitBulk: (inputs: InputProvideRequest[]) => Promise<void>;
  isSubmitting?: boolean;
}

const inputTypeConfig: Record<string, { 
  icon: React.ElementType; 
  label: string;
  placeholder: string;
}> = {
  credentials: {
    icon: Key,
    label: 'Credentials',
    placeholder: 'Enter API key or credentials...'
  },
  text: {
    icon: FileText,
    label: 'Text',
    placeholder: 'Enter your response...'
  },
  file: {
    icon: FileText,
    label: 'File',
    placeholder: 'Select or drag a file...'
  },
  confirmation: {
    icon: CheckSquare,
    label: 'Confirmation',
    placeholder: 'Confirm to proceed'
  },
  selection: {
    icon: List,
    label: 'Selection',
    placeholder: 'Select an option...'
  },
  number: {
    icon: Hash,
    label: 'Number',
    placeholder: 'Enter a number...'
  },
  url: {
    icon: Link,
    label: 'URL',
    placeholder: 'Enter URL...'
  }
};

const priorityConfig: Record<string, { 
  color: string; 
  bgColor: string;
  label: string;
  sortOrder: number;
}> = {
  blocking: {
    color: 'text-red-400',
    bgColor: 'bg-red-500/20 border-red-500/30',
    label: 'Blocking',
    sortOrder: 0
  },
  high: {
    color: 'text-orange-400',
    bgColor: 'bg-orange-500/20 border-orange-500/30',
    label: 'High',
    sortOrder: 1
  },
  medium: {
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/20 border-yellow-500/30',
    label: 'Medium',
    sortOrder: 2
  },
  low: {
    color: 'text-gray-400',
    bgColor: 'bg-gray-500/20 border-gray-500/30',
    label: 'Low',
    sortOrder: 3
  }
};

interface InputFormData {
  [key: string]: string;
}

export function InputRequestPanel({ inputs, onSubmit, onSubmitBulk, isSubmitting = false }: InputRequestPanelProps) {
  const [formData, setFormData] = useState<InputFormData>({});
  const [expandedInputs, setExpandedInputs] = useState<Set<string>>(new Set());
  const [submittingKey, setSubmittingKey] = useState<string | null>(null);
  
  // Sort inputs by priority (blocking first)
  const sortedInputs = useMemo(() => {
    return [...inputs].sort((a, b) => {
      const aPriority = priorityConfig[a.priority]?.sortOrder ?? 999;
      const bPriority = priorityConfig[b.priority]?.sortOrder ?? 999;
      if (aPriority !== bPriority) return aPriority - bPriority;
      // Secondary sort by blocking count (more blocking = higher priority)
      return b.blocking_count - a.blocking_count;
    });
  }, [inputs]);
  
  // Group by priority
  const groupedInputs = useMemo(() => {
    const groups: Record<string, UserInputRequest[]> = {
      blocking: [],
      high: [],
      medium: [],
      low: []
    };
    sortedInputs.forEach(input => {
      const group = groups[input.priority] || groups.low;
      group.push(input);
    });
    return groups;
  }, [sortedInputs]);
  
  const toggleExpanded = (key: string) => {
    setExpandedInputs(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };
  
  const handleInputChange = (key: string, value: string) => {
    setFormData(prev => ({ ...prev, [key]: value }));
  };
  
  const handleSubmitSingle = async (input: UserInputRequest) => {
    const value = formData[input.input_key];
    if (!value && input.input_type !== 'confirmation') return;
    
    setSubmittingKey(input.input_key);
    try {
      await onSubmit({
        input_key: input.input_key,
        value: input.input_type === 'confirmation' ? 'confirmed' : value
      });
      // Clear the submitted value
      setFormData(prev => {
        const next = { ...prev };
        delete next[input.input_key];
        return next;
      });
    } finally {
      setSubmittingKey(null);
    }
  };
  
  const handleSubmitAll = async () => {
    const filledInputs = sortedInputs
      .filter(input => {
        if (input.input_type === 'confirmation') return true;
        return !!formData[input.input_key];
      })
      .map(input => ({
        input_key: input.input_key,
        value: input.input_type === 'confirmation' ? 'confirmed' : formData[input.input_key]
      }));
    
    if (filledInputs.length === 0) return;
    
    await onSubmitBulk(filledInputs);
    setFormData({});
  };
  
  const filledCount = sortedInputs.filter(input => 
    input.input_type === 'confirmation' || !!formData[input.input_key]
  ).length;
  
  const blockingInputs = groupedInputs.blocking;
  const hasBlockingInputs = blockingInputs.length > 0;

  if (inputs.length === 0) {
    return (
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 text-center">
        <CheckSquare className="h-8 w-8 text-neon-cyan mx-auto mb-2" />
        <p className="text-gray-400">No inputs required at this time</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header with bulk submit */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-medium text-white">Required Inputs</h3>
          <p className="text-sm text-gray-400">
            {hasBlockingInputs ? (
              <span className="text-red-400">
                {blockingInputs.length} blocking input{blockingInputs.length > 1 ? 's' : ''} preventing progress
              </span>
            ) : (
              `${inputs.length} input${inputs.length > 1 ? 's' : ''} needed`
            )}
          </p>
        </div>
        
        {filledCount > 1 && (
          <button
            onClick={handleSubmitAll}
            disabled={isSubmitting}
            className="btn-primary flex items-center gap-2"
          >
            {isSubmitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Zap className="h-4 w-4" />
            )}
            Submit All ({filledCount})
          </button>
        )}
      </div>
      
      {/* Blocking Inputs Alert */}
      {hasBlockingInputs && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <h4 className="text-sm font-medium text-red-400">Blocking Inputs</h4>
              <p className="text-xs text-gray-400 mt-1">
                These inputs are required before the campaign can proceed. 
                Providing them will unblock {blockingInputs.reduce((sum, i) => sum + i.blocking_count, 0)} task{blockingInputs.reduce((sum, i) => sum + i.blocking_count, 0) !== 1 ? 's' : ''}.
              </p>
            </div>
          </div>
        </div>
      )}
      
      {/* Input List */}
      <div className="space-y-3">
        {sortedInputs.map(input => {
          const typeConfig = inputTypeConfig[input.input_type] || inputTypeConfig.text;
          const prioConfig = priorityConfig[input.priority] || priorityConfig.low;
          const TypeIcon = typeConfig.icon;
          const isExpanded = expandedInputs.has(input.input_key);
          const value = formData[input.input_key] || '';
          const isSubmittingThis = submittingKey === input.input_key;
          
          return (
            <div 
              key={input.input_key}
              className={`bg-gray-900/50 border rounded-lg overflow-hidden transition-colors ${
                input.priority === 'blocking' 
                  ? 'border-red-500/30 hover:border-red-500/50' 
                  : 'border-gray-800 hover:border-gray-700'
              }`}
            >
              {/* Input Header */}
              <button
                onClick={() => toggleExpanded(input.input_key)}
                className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-gray-800/30 transition-colors"
              >
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <TypeIcon className={`h-4 w-4 flex-shrink-0 ${prioConfig.color}`} />
                  <div className="flex-1 min-w-0">
                    <h4 className="text-sm font-medium text-white truncate">{input.title}</h4>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={`text-xs px-1.5 py-0.5 rounded border ${prioConfig.bgColor} ${prioConfig.color}`}>
                        {prioConfig.label}
                      </span>
                      {input.blocking_count > 0 && (
                        <span className="text-xs text-gray-500">
                          Blocks {input.blocking_count} task{input.blocking_count > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                
                <div className="flex items-center gap-2 flex-shrink-0">
                  {value && (
                    <span className="text-xs text-green-400">Filled</span>
                  )}
                  {isExpanded ? (
                    <ChevronUp className="h-4 w-4 text-gray-500" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-gray-500" />
                  )}
                </div>
              </button>
              
              {/* Input Form */}
              {isExpanded && (
                <div className="px-4 pb-4 border-t border-gray-800">
                  {input.description && (
                    <p className="text-xs text-gray-400 py-3">{input.description}</p>
                  )}
                  
                  <div className="flex gap-2">
                    {input.input_type === 'confirmation' ? (
                      <button
                        onClick={() => handleSubmitSingle(input)}
                        disabled={isSubmittingThis || isSubmitting}
                        className="btn-primary flex items-center gap-2"
                      >
                        {isSubmittingThis ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <CheckSquare className="h-4 w-4" />
                        )}
                        Confirm
                      </button>
                    ) : input.input_type === 'selection' && input.options ? (
                      <div className="flex-1 flex gap-2">
                        <select
                          value={value}
                          onChange={(e) => handleInputChange(input.input_key, e.target.value)}
                          className="input-field flex-1"
                        >
                          <option value="">Select an option...</option>
                          {input.options.map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleSubmitSingle(input)}
                          disabled={!value || isSubmittingThis || isSubmitting}
                          className="btn-primary px-3"
                        >
                          {isSubmittingThis ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Send className="h-4 w-4" />
                          )}
                        </button>
                      </div>
                    ) : (
                      <div className="flex-1 flex gap-2">
                        <input
                          type={input.input_type === 'credentials' ? 'password' : 
                                input.input_type === 'number' ? 'number' : 
                                input.input_type === 'url' ? 'url' : 'text'}
                          value={value}
                          onChange={(e) => handleInputChange(input.input_key, e.target.value)}
                          placeholder={input.suggested_value || input.default_value || typeConfig.placeholder}
                          className="input-field flex-1"
                        />
                        <button
                          onClick={() => handleSubmitSingle(input)}
                          disabled={!value || isSubmittingThis || isSubmitting}
                          className="btn-primary px-3"
                        >
                          {isSubmittingThis ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Send className="h-4 w-4" />
                          )}
                        </button>
                      </div>
                    )}
                  </div>
                  
                  {input.suggested_value && input.input_type !== 'confirmation' && input.input_type !== 'credentials' && (
                    <p className="text-xs text-gray-500 mt-2">
                      Suggested: <span className="text-gray-400">{input.suggested_value}</span>
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
