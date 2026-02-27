import { useState, useCallback, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toolsService } from '@/services/tools';
import type { Tool, ToolExecution, ToolExecuteRequest } from '@/types';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import {
  Play,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  Copy,
  Check,
  ShieldAlert,
  Gauge,
  ServerOff,
  Timer,
  History,
  Zap,
  Info,
  FileWarning,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { InvoiceQR } from '@/components/shared/InvoiceQR';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ToolWorkbenchProps {
  tool: Tool;
}

/** A single field derived from JSON Schema properties */
interface SchemaField {
  name: string;
  type: string;
  description?: string;
  required: boolean;
  default?: any;
  enum?: any[];
  maxLength?: number;
  minimum?: number;
  maximum?: number;
}

/** Phase of an execution attempt visible to the user */
type ExecutionPhase =
  | 'idle'
  | 'queued'
  | 'executing'
  | 'completed'
  | 'failed'
  | 'rate_limited'
  | 'approval_required'
  | 'resource_unavailable'
  | 'resource_busy'
  | 'queue_timeout';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseSchemaFields(schema: Record<string, any> | null): SchemaField[] {
  if (!schema || !schema.properties) return [];
  const required: string[] = schema.required || [];
  return Object.entries(schema.properties).map(([name, propRaw]) => {
    const prop = propRaw as Record<string, any>;
    return {
      name,
      type: prop.type || 'string',
      description: prop.description,
      required: required.includes(name),
      default: prop.default,
      enum: prop.enum,
      maxLength: prop.maxLength,
      minimum: prop.minimum,
      maximum: prop.maximum,
    };
  });
}

/** Categorise the error string returned by the backend */
function classifyError(error: string | null): ExecutionPhase {
  if (!error) return 'failed';
  if (error.startsWith('RATE_LIMIT_EXCEEDED:')) return 'rate_limited';
  if (error.startsWith('APPROVAL_REQUIRED:')) return 'approval_required';
  if (error.startsWith('RESOURCE_UNAVAILABLE:')) return 'resource_unavailable';
  if (error.startsWith('RESOURCE_BUSY:')) return 'resource_busy';
  if (error.startsWith('QUEUE_TIMEOUT:')) return 'queue_timeout';
  return 'failed';
}

function stripErrorPrefix(error: string): string {
  return error.replace(/^(RATE_LIMIT_EXCEEDED|APPROVAL_REQUIRED|RESOURCE_UNAVAILABLE|RESOURCE_BUSY|QUEUE_TIMEOUT|QUEUE_ERROR):?\s*/i, '');
}

const EXECUTION_STATUS_STYLES: Record<string, { bg: string; text: string; icon: React.ComponentType<{ className?: string }> }> = {
  completed: { bg: 'bg-green-500/10 border-green-500/30', text: 'text-green-400', icon: CheckCircle },
  failed: { bg: 'bg-red-500/10 border-red-500/30', text: 'text-red-400', icon: XCircle },
  timeout: { bg: 'bg-orange-500/10 border-orange-500/30', text: 'text-orange-400', icon: Timer },
  cancelled: { bg: 'bg-gray-500/10 border-gray-500/30', text: 'text-gray-400', icon: XCircle },
  pending: { bg: 'bg-blue-500/10 border-blue-500/30', text: 'text-blue-400', icon: Clock },
  running: { bg: 'bg-cyan-500/10 border-cyan-500/30', text: 'text-cyan-400', icon: Loader2 },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Renders a single form field based on JSON Schema property */
function SchemaFieldInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: any;
  onChange: (value: any) => void;
  disabled: boolean;
}) {
  const baseClasses =
    'w-full bg-gray-900/50 border border-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-neon-cyan/50 focus:border-neon-cyan/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

  // Enum → select
  if (field.enum && field.enum.length > 0) {
    return (
      <select
        value={value ?? field.default ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        disabled={disabled}
        className={baseClasses}
      >
        {!field.required && <option value="">— none —</option>}
        {field.enum.map((opt: any) => (
          <option key={String(opt)} value={opt}>
            {String(opt)}
          </option>
        ))}
      </select>
    );
  }

  // Boolean → checkbox
  if (field.type === 'boolean') {
    return (
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={value ?? field.default ?? false}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
          className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-neon-cyan focus:ring-neon-cyan/50"
        />
        <span className="text-sm text-gray-300">{field.description || field.name}</span>
      </label>
    );
  }

  // Number / integer
  if (field.type === 'number' || field.type === 'integer') {
    return (
      <input
        type="number"
        value={value ?? ''}
        onChange={(e) => {
          const v = e.target.value;
          if (v === '') {
            onChange(undefined);
          } else {
            onChange(field.type === 'integer' ? parseInt(v, 10) : parseFloat(v));
          }
        }}
        min={field.minimum}
        max={field.maximum}
        step={field.type === 'integer' ? 1 : 'any'}
        placeholder={field.default !== undefined ? `Default: ${field.default}` : ''}
        disabled={disabled}
        className={baseClasses}
      />
    );
  }

  // String — use textarea for long text fields
  const isLongText =
    (field.maxLength && field.maxLength > 200) ||
    field.name === 'text' ||
    field.name === 'prompt' ||
    field.name === 'lyrics' ||
    field.name === 'description' ||
    field.name === 'instruct' ||
    field.name === 'voice_description' ||
    field.name === 'query';
  if (isLongText) {
    return (
      <textarea
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        placeholder={field.default !== undefined ? `Default: ${field.default}` : field.description || ''}
        disabled={disabled}
        rows={4}
        maxLength={field.maxLength}
        className={`${baseClasses} resize-y min-h-[80px]`}
      />
    );
  }

  // Default: text input
  return (
    <input
      type="text"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || undefined)}
      placeholder={field.default !== undefined ? `Default: ${field.default}` : field.description || ''}
      disabled={disabled}
      maxLength={field.maxLength}
      className={baseClasses}
    />
  );
}

/** Structured error banner with icon and actionable info */
function ExecutionAlert({
  phase,
  error,
  queuePosition,
}: {
  phase: ExecutionPhase;
  error: string | null;
  queuePosition?: number | null;
}) {
  const cleaned = error ? stripErrorPrefix(error) : '';

  const configs: Record<string, { icon: React.ComponentType<{ className?: string }>; color: string; title: string }> = {
    rate_limited: { icon: Gauge, color: 'border-yellow-500/30 bg-yellow-500/10 text-yellow-400', title: 'Rate Limit Exceeded' },
    approval_required: { icon: ShieldAlert, color: 'border-purple-500/30 bg-purple-500/10 text-purple-400', title: 'Approval Required' },
    resource_unavailable: { icon: ServerOff, color: 'border-red-500/30 bg-red-500/10 text-red-400', title: 'Resource Unavailable' },
    resource_busy: { icon: Clock, color: 'border-blue-500/30 bg-blue-500/10 text-blue-400', title: 'Resource Busy' },
    queue_timeout: { icon: Timer, color: 'border-orange-500/30 bg-orange-500/10 text-orange-400', title: 'Queue Timeout' },
    failed: { icon: XCircle, color: 'border-red-500/30 bg-red-500/10 text-red-400', title: 'Execution Failed' },
  };

  const cfg = configs[phase] || configs.failed;
  const Icon = cfg.icon;

  return (
    <div className={`border rounded-lg p-4 ${cfg.color}`}>
      <div className="flex items-start gap-3">
        <Icon className="h-5 w-5 flex-shrink-0 mt-0.5" />
        <div className="space-y-1 min-w-0">
          <p className="font-semibold">{cfg.title}</p>
          {cleaned && <p className="text-sm opacity-80 break-words">{cleaned}</p>}
          {phase === 'resource_busy' && queuePosition != null && (
            <p className="text-sm opacity-80">Queue position: #{queuePosition}</p>
          )}
          {phase === 'approval_required' && (
            <p className="text-sm opacity-80">An admin must approve this tool execution before it can proceed.</p>
          )}
          {phase === 'queue_timeout' && (
            <p className="text-sm opacity-80">The resource queue timed out. Try again later or increase the queue timeout.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/** Compact execution history row */
function ExecutionHistoryRow({
  execution,
  onReplay,
}: {
  execution: ToolExecution;
  onReplay?: (params: Record<string, any>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const style = EXECUTION_STATUS_STYLES[execution.status] || EXECUTION_STATUS_STYLES.failed;
  const Icon = style.icon;

  return (
    <div className={`border rounded-lg overflow-hidden ${style.bg}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/5 transition-colors"
      >
        <Icon className={`h-4 w-4 flex-shrink-0 ${style.text} ${execution.status === 'running' ? 'animate-spin' : ''}`} />
        <span className={`text-sm font-medium ${style.text} capitalize`}>{execution.status}</span>
        {execution.duration_ms != null && (
          <span className="text-xs text-gray-500">{(execution.duration_ms / 1000).toFixed(1)}s</span>
        )}
        {execution.error && (
          <span className="text-xs text-gray-500 truncate flex-1">{stripErrorPrefix(execution.error).slice(0, 80)}</span>
        )}
        <span className="text-xs text-gray-500 flex-shrink-0 ml-auto">
          {execution.id ? formatDistanceToNow(new Date(), { addSuffix: true }) : ''}
        </span>
        {expanded ? <ChevronUp className="h-4 w-4 text-gray-500" /> : <ChevronDown className="h-4 w-4 text-gray-500" />}
      </button>
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-gray-800">
          {execution.output && (
            <div className="mt-3">
              <span className="text-xs text-gray-400 block mb-1">Output</span>
              <div className="border border-gray-700 rounded-lg overflow-hidden max-h-[300px]">
                <CodeMirror
                  value={JSON.stringify(execution.output, null, 2)}
                  extensions={[json()]}
                  theme="dark"
                  editable={false}
                  basicSetup={{ lineNumbers: false, foldGutter: true, highlightActiveLine: false }}
                  style={{ fontSize: '12px', backgroundColor: 'rgba(0,0,0,0.3)' }}
                />
              </div>
            </div>
          )}
          {execution.error && (
            <div className="mt-3">
              <span className="text-xs text-gray-400 block mb-1">Error</span>
              <p className="text-sm text-red-400 bg-red-500/5 rounded-lg p-3 break-words">{execution.error}</p>
            </div>
          )}
          {onReplay && execution.output === null && execution.error === null ? null : (
            <div className="flex items-center gap-2 pt-1">
              {execution.cost_units != null && execution.cost_units > 0 && (
                <span className="text-xs text-gray-500">Cost: {execution.cost_units} units</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ToolWorkbench({ tool }: ToolWorkbenchProps) {
  const queryClient = useQueryClient();
  const fields = parseSchemaFields(tool.input_schema);
  const hasSchema = fields.length > 0;

  // Form state — structured when schema exists, raw JSON when not
  const [formValues, setFormValues] = useState<Record<string, any>>(() => {
    const defaults: Record<string, any> = {};
    for (const f of fields) {
      if (f.default !== undefined) defaults[f.name] = f.default;
      else if (f.required && f.enum?.length) defaults[f.name] = f.enum[0];
    }
    return defaults;
  });
  const [rawJson, setRawJson] = useState('{\n  \n}');
  const [rawJsonError, setRawJsonError] = useState<string | null>(null);

  // Queue timeout config
  const [queueTimeout, setQueueTimeout] = useState<number>(60);

  // Execution state
  const [lastResult, setLastResult] = useState<ToolExecution | null>(null);
  const [phase, setPhase] = useState<ExecutionPhase>('idle');
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Clipboard
  const [copied, setCopied] = useState(false);

  // History
  const [showHistory, setShowHistory] = useState(false);

  const { data: executions = [] } = useQuery({
    queryKey: ['tool-executions', tool.id],
    queryFn: () => toolsService.listExecutions(tool.id, 10),
    enabled: showHistory,
    refetchInterval: phase === 'executing' || phase === 'queued' ? 5000 : false,
  });

  // Elapsed timer
  useEffect(() => {
    if (phase === 'executing' || phase === 'queued') {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [phase]);

  // Build params from form state
  const buildParams = useCallback((): Record<string, any> | null => {
    if (hasSchema) {
      const params: Record<string, any> = {};
      for (const f of fields) {
        const val = formValues[f.name];
        if (val !== undefined && val !== '' && val !== null) {
          params[f.name] = val;
        } else if (f.required) {
          // Missing required field
          return null;
        }
      }
      return params;
    } else {
      try {
        const parsed = JSON.parse(rawJson);
        setRawJsonError(null);
        return parsed;
      } catch (e: any) {
        setRawJsonError(e.message);
        return null;
      }
    }
  }, [hasSchema, fields, formValues, rawJson]);

  // Validation
  const missingRequired = hasSchema
    ? fields.filter((f) => f.required && (formValues[f.name] === undefined || formValues[f.name] === '' || formValues[f.name] === null))
    : [];

  // Execute mutation
  const executeMutation = useMutation({
    mutationFn: (request: ToolExecuteRequest) => toolsService.executeTool(tool.id, request),
    onMutate: () => {
      setPhase('executing');
      setLastResult(null);
    },
    onSuccess: (result) => {
      setLastResult(result);
      if (result.success) {
        setPhase('completed');
        // Auto-refresh wallet/budget queries after LND payment actions
        if (tool.slug === 'lnd-lightning') {
          queryClient.invalidateQueries({ queryKey: ['wallet-summary'] });
          queryClient.invalidateQueries({ queryKey: ['wallet-payments'] });
          queryClient.invalidateQueries({ queryKey: ['wallet-invoices'] });
          queryClient.invalidateQueries({ queryKey: ['wallet-transactions'] });
          queryClient.invalidateQueries({ queryKey: ['wallet-channels'] });
          queryClient.invalidateQueries({ queryKey: ['bitcoin-budget-global'] });
          queryClient.invalidateQueries({ queryKey: ['bitcoin-transactions'] });
        }
      } else if (result.error) {
        setPhase(classifyError(result.error));
      } else {
        setPhase('failed');
      }
      queryClient.invalidateQueries({ queryKey: ['tool-executions', tool.id] });
    },
    onError: (error: any) => {
      const msg = error?.response?.data?.detail || error?.message || 'Unknown error';
      setLastResult({
        id: '',
        tool_id: tool.id,
        tool_name: tool.name,
        status: 'failed',
        success: false,
        output: null,
        error: msg,
        duration_ms: null,
        cost_units: null,
        job_id: null,
        queue_position: null,
      });
      setPhase('failed');
    },
  });

  const handleExecute = () => {
    const params = buildParams();
    if (params === null) return;
    executeMutation.mutate({
      params,
      queue_timeout: queueTimeout,
      wait_for_resource: true,
    });
  };

  const handleReset = () => {
    setPhase('idle');
    setLastResult(null);
    setElapsed(0);
  };

  const handleCopyOutput = async () => {
    if (!lastResult?.output) return;
    await navigator.clipboard.writeText(JSON.stringify(lastResult.output, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleReplayFromHistory = (params: Record<string, any>) => {
    if (hasSchema) {
      setFormValues(params);
    } else {
      setRawJson(JSON.stringify(params, null, 2));
    }
    handleReset();
  };

  const isRunning = phase === 'executing' || phase === 'queued';
  const canExecute = !isRunning && (hasSchema ? missingRequired.length === 0 : !rawJsonError);
  const isDeprecated = tool.status === 'deprecated';

  return (
    <div className="space-y-6">
      {/* Deprecated warning */}
      {isDeprecated && (
        <div className="border border-orange-500/30 bg-orange-500/10 rounded-lg p-4 flex items-start gap-3">
          <FileWarning className="h-5 w-5 text-orange-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-orange-400 font-medium">Deprecated Tool</p>
            <p className="text-sm text-orange-400/80">
              This tool is deprecated and may be removed in the future. It still functions but is no longer recommended.
            </p>
          </div>
        </div>
      )}

      {/* Input section */}
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Zap className="h-5 w-5 text-neon-cyan" />
            Parameters
          </h3>
          {tool.input_schema && (
            <span className="text-xs text-gray-500 flex items-center gap-1">
              <Info className="h-3 w-3" />
              Fields generated from tool schema
            </span>
          )}
        </div>

        {hasSchema ? (
          <div className="space-y-4">
            {fields.map((field) => (
              <div key={field.name}>
                <label className="block text-sm font-medium text-gray-300 mb-1">
                  {field.name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                  {field.required && <span className="text-red-400 ml-1">*</span>}
                </label>
                {field.description && field.type !== 'boolean' && (
                  <p className="text-xs text-gray-500 mb-1.5">{field.description}</p>
                )}
                <SchemaFieldInput
                  field={field}
                  value={formValues[field.name]}
                  onChange={(val) =>
                    setFormValues((prev) => ({ ...prev, [field.name]: val }))
                  }
                  disabled={isRunning}
                />
              </div>
            ))}
          </div>
        ) : (
          <div>
            <p className="text-sm text-gray-400 mb-2">
              This tool does not have a defined input schema. Enter parameters as JSON.
            </p>
            <div className="border border-gray-700 rounded-lg overflow-hidden">
              <CodeMirror
                value={rawJson}
                onChange={(val) => {
                  setRawJson(val);
                  try {
                    JSON.parse(val);
                    setRawJsonError(null);
                  } catch (e: any) {
                    setRawJsonError(e.message);
                  }
                }}
                extensions={[json()]}
                theme="dark"
                editable={!isRunning}
                basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
                style={{ fontSize: '14px', backgroundColor: 'rgba(0,0,0,0.3)', minHeight: '120px' }}
              />
            </div>
            {rawJsonError && (
              <p className="text-xs text-red-400 mt-1">Invalid JSON: {rawJsonError}</p>
            )}
          </div>
        )}

        {/* Queue timeout */}
        <div className="mt-4 pt-4 border-t border-gray-800">
          <div className="flex items-center gap-4">
            <label className="text-sm text-gray-400 flex items-center gap-1.5 flex-shrink-0">
              <Clock className="h-3.5 w-3.5" />
              Queue timeout
            </label>
            <select
              value={queueTimeout}
              onChange={(e) => setQueueTimeout(parseInt(e.target.value))}
              disabled={isRunning}
              className="bg-gray-900/50 border border-gray-700 rounded-lg px-2 py-1 text-sm text-white focus:outline-none focus:ring-1 focus:ring-neon-cyan/50"
            >
              <option value={15}>15s</option>
              <option value={30}>30s</option>
              <option value={60}>60s</option>
              <option value={120}>2 min</option>
              <option value={300}>5 min</option>
              <option value={600}>10 min</option>
            </select>
            {tool.resource_ids && tool.resource_ids.length > 0 && (
              <span className="text-xs text-gray-500">
                This tool requires GPU resources and may queue behind other jobs.
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Execute button */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleExecute}
          disabled={!canExecute}
          className={`flex items-center gap-2 px-6 py-3 rounded-lg font-semibold transition-all ${
            canExecute
              ? 'bg-gradient-to-r from-neon-cyan to-neon-blue text-gray-900 hover:shadow-lg hover:shadow-neon-cyan/25'
              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
          }`}
        >
          {isRunning ? (
            <>
              <Loader2 className="h-5 w-5 animate-spin" />
              {phase === 'queued' ? 'Queued' : 'Executing'}
              <span className="text-sm opacity-75">({elapsed}s)</span>
            </>
          ) : (
            <>
              <Play className="h-5 w-5" />
              Execute Tool
            </>
          )}
        </button>

        {hasSchema && missingRequired.length > 0 && !isRunning && (
          <span className="text-sm text-yellow-400 flex items-center gap-1">
            <AlertTriangle className="h-4 w-4" />
            Missing required: {missingRequired.map((f) => f.name).join(', ')}
          </span>
        )}

        {(phase === 'completed' || phase === 'failed' || phase === 'rate_limited' || phase === 'approval_required' || phase === 'resource_unavailable' || phase === 'resource_busy' || phase === 'queue_timeout') && (
          <button
            onClick={handleReset}
            className="flex items-center gap-2 px-4 py-2 text-gray-400 hover:text-white transition-colors"
          >
            <RotateCcw className="h-4 w-4" />
            Reset
          </button>
        )}
      </div>

      {/* Result display */}
      {lastResult && phase !== 'idle' && phase !== 'executing' && phase !== 'queued' && (
        <div className="space-y-4">
          {/* Error conditions */}
          {phase !== 'completed' && (
            <ExecutionAlert
              phase={phase}
              error={lastResult.error}
              queuePosition={lastResult.queue_position}
            />
          )}

          {/* Success output */}
          {phase === 'completed' && (
            <div className="bg-gray-900/50 border border-green-500/30 rounded-lg p-6">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold text-green-400 flex items-center gap-2">
                  <CheckCircle className="h-5 w-5" />
                  Result
                </h3>
                <div className="flex items-center gap-3">
                  {lastResult.duration_ms != null && (
                    <span className="text-xs text-gray-500 flex items-center gap-1">
                      <Timer className="h-3 w-3" />
                      {(lastResult.duration_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                  {lastResult.cost_units != null && lastResult.cost_units > 0 && (
                    <span className="text-xs text-gray-500">{lastResult.cost_units} cost units</span>
                  )}
                  {lastResult.output && (
                    <button
                      onClick={handleCopyOutput}
                      className="flex items-center gap-1 text-xs text-gray-400 hover:text-neon-cyan transition-colors"
                    >
                      {copied ? <Check className="h-3.5 w-3.5 text-green-400" /> : <Copy className="h-3.5 w-3.5" />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  )}
                </div>
              </div>

              {lastResult.output ? (
                <>
                  {/* QR code for Lightning invoices */}
                  {typeof lastResult.output === 'object' && lastResult.output !== null && 'payment_request' in lastResult.output && (
                    <div className="flex justify-center mb-4">
                      <InvoiceQR
                        paymentRequest={String((lastResult.output as Record<string, unknown>).payment_request)}
                        size={220}
                        label="Scan to Pay"
                      />
                    </div>
                  )}
                  <div className="border border-gray-700 rounded-lg overflow-hidden max-h-[500px] overflow-y-auto">
                    <CodeMirror
                      value={JSON.stringify(lastResult.output, null, 2)}
                      extensions={[json()]}
                      theme="dark"
                      editable={false}
                      basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: false }}
                      style={{ fontSize: '13px', backgroundColor: 'rgba(0,0,0,0.3)' }}
                    />
                  </div>
                </>
              ) : (
                <p className="text-sm text-gray-400 italic">Tool completed successfully with no output data.</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Execution history */}
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg">
        <button
          onClick={() => setShowHistory((h) => !h)}
          className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-white/5 transition-colors"
        >
          <span className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <History className="h-4 w-4 text-gray-400" />
            Recent Executions
          </span>
          {showHistory ? (
            <ChevronUp className="h-4 w-4 text-gray-400" />
          ) : (
            <ChevronDown className="h-4 w-4 text-gray-400" />
          )}
        </button>
        {showHistory && (
          <div className="px-6 pb-4 space-y-2">
            {executions.length === 0 ? (
              <p className="text-sm text-gray-500 py-2">No execution history yet.</p>
            ) : (
              executions.map((exec) => (
                <ExecutionHistoryRow
                  key={exec.id}
                  execution={exec}
                  onReplay={handleReplayFromHistory}
                />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
