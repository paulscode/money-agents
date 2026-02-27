import { useState, useEffect } from 'react';
import { Terminal, Code, Server, Globe, ChevronDown, ChevronUp, Plus, Trash2, AlertCircle } from 'lucide-react';

export type InterfaceType = 'rest_api' | 'cli' | 'python_sdk' | 'mcp' | null;

interface InterfaceConfigEditorProps {
  interfaceType: InterfaceType;
  interfaceConfig: Record<string, any> | null;
  onTypeChange: (type: InterfaceType) => void;
  onConfigChange: (config: Record<string, any> | null) => void;
  inputSchema: Record<string, any> | null;
  outputSchema: Record<string, any> | null;
  onInputSchemaChange: (schema: Record<string, any> | null) => void;
  onOutputSchemaChange: (schema: Record<string, any> | null) => void;
  timeoutSeconds: number | null;
  onTimeoutChange: (timeout: number | null) => void;
}

const INTERFACE_TYPES: Array<{ value: InterfaceType; label: string; icon: React.ElementType; description: string }> = [
  { value: null, label: 'Not Configured', icon: AlertCircle, description: 'Tool execution not yet configured' },
  { value: 'rest_api', label: 'REST API', icon: Globe, description: 'HTTP-based API calls with configurable endpoints' },
  { value: 'cli', label: 'CLI', icon: Terminal, description: 'Command-line tool execution with templates' },
  { value: 'python_sdk', label: 'Python SDK', icon: Code, description: 'Python module/class-based SDK integration' },
  { value: 'mcp', label: 'MCP', icon: Server, description: 'Model Context Protocol server interface' },
];

interface RestApiConfig {
  base_url: string;
  endpoints: Record<string, { method: string; path: string; headers?: Record<string, string> }>;
  auth_type?: 'none' | 'bearer' | 'api_key' | 'basic';
  auth_config?: Record<string, string>;
}

interface CliConfig {
  command: string;
  working_dir?: string;
  templates: Record<string, { args: string[]; env?: Record<string, string> }>;
}

interface PythonSdkConfig {
  module: string;
  class?: string;
  init_args?: Record<string, any>;
  method: string;
  method_kwargs_mapping?: Record<string, string>;
}

interface McpConfig {
  server_command?: string[];
  server_url?: string;
  transport: 'stdio' | 'http';
  tool_name: string;
}

export function InterfaceConfigEditor({
  interfaceType,
  interfaceConfig,
  onTypeChange,
  onConfigChange,
  inputSchema,
  outputSchema,
  onInputSchemaChange,
  onOutputSchemaChange,
  timeoutSeconds,
  onTimeoutChange,
}: InterfaceConfigEditorProps) {
  const [expanded, setExpanded] = useState(!!interfaceType);
  const [rawJsonMode, setRawJsonMode] = useState(false);
  const [rawJson, setRawJson] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Sync raw JSON when config changes externally
  useEffect(() => {
    if (interfaceConfig) {
      setRawJson(JSON.stringify(interfaceConfig, null, 2));
    } else {
      setRawJson('{}');
    }
  }, [interfaceConfig]);

  const handleRawJsonChange = (value: string) => {
    setRawJson(value);
    try {
      const parsed = JSON.parse(value);
      setJsonError(null);
      onConfigChange(parsed);
    } catch {
      setJsonError('Invalid JSON');
    }
  };

  const getDefaultConfig = (type: InterfaceType): Record<string, any> => {
    switch (type) {
      case 'rest_api':
        return {
          base_url: 'https://api.example.com',
          endpoints: {
            default: { method: 'GET', path: '/endpoint' }
          },
          auth_type: 'none'
        };
      case 'cli':
        return {
          command: 'tool-command',
          working_dir: '/tmp/tool_workspace',
          templates: {
            default: { args: ['--input', '{{input}}'], env: {} }
          }
        };
      case 'python_sdk':
        return {
          module: 'example_module',
          class: 'ExampleClient',
          init_args: {},
          method: 'execute',
          method_kwargs_mapping: {}
        };
      case 'mcp':
        return {
          transport: 'stdio',
          server_command: ['npx', '-y', '@modelcontextprotocol/server-example'],
          tool_name: 'example_tool'
        };
      default:
        return {};
    }
  };

  const handleTypeChange = (type: InterfaceType) => {
    onTypeChange(type);
    if (type && !interfaceConfig) {
      onConfigChange(getDefaultConfig(type));
    }
  };

  const selectedType = INTERFACE_TYPES.find(t => t.value === interfaceType);

  return (
    <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <Server className="h-5 w-5 text-neon-cyan" />
          <div>
            <h2 className="text-xl font-semibold text-white">Execution Interface</h2>
            <p className="text-sm text-gray-400">
              {selectedType ? `${selectedType.label} - ${selectedType.description}` : 'Configure how this tool is executed'}
            </p>
          </div>
        </div>
        {expanded ? (
          <ChevronUp className="h-5 w-5 text-gray-400" />
        ) : (
          <ChevronDown className="h-5 w-5 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="mt-6 space-y-6">
          {/* Interface Type Selector */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">
              Interface Type
            </label>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {INTERFACE_TYPES.map((type) => {
                const Icon = type.icon;
                const isSelected = interfaceType === type.value;
                return (
                  <button
                    key={type.value ?? 'none'}
                    type="button"
                    onClick={() => handleTypeChange(type.value)}
                    className={`p-3 rounded-lg border text-left transition-all ${
                      isSelected
                        ? 'border-neon-cyan bg-neon-cyan/10 text-neon-cyan'
                        : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:border-gray-600'
                    }`}
                  >
                    <Icon className={`h-5 w-5 mb-1 ${isSelected ? 'text-neon-cyan' : 'text-gray-500'}`} />
                    <div className="text-sm font-medium">{type.label}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {interfaceType && (
            <>
              {/* Timeout */}
              <div>
                <label htmlFor="timeout" className="block text-sm font-medium text-gray-300 mb-2">
                  Timeout (seconds)
                </label>
                <input
                  id="timeout"
                  type="number"
                  min={1}
                  max={3600}
                  value={timeoutSeconds ?? 30}
                  onChange={(e) => onTimeoutChange(e.target.value ? parseInt(e.target.value) : null)}
                  className="w-32 px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                />
                <p className="mt-1 text-xs text-gray-500">Maximum execution time before timeout</p>
              </div>

              {/* Toggle JSON mode */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setRawJsonMode(!rawJsonMode)}
                  className={`px-3 py-1 text-sm rounded-lg transition-colors ${
                    rawJsonMode
                      ? 'bg-neon-cyan/20 text-neon-cyan'
                      : 'bg-gray-800 text-gray-400 hover:text-white'
                  }`}
                >
                  {rawJsonMode ? 'Visual Editor' : 'JSON Editor'}
                </button>
              </div>

              {/* Configuration Editor */}
              {rawJsonMode ? (
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Interface Configuration (JSON)
                  </label>
                  <textarea
                    value={rawJson}
                    onChange={(e) => handleRawJsonChange(e.target.value)}
                    rows={15}
                    className={`w-full px-4 py-2 bg-gray-900/50 border rounded-lg text-white font-mono text-sm focus:ring-1 ${
                      jsonError
                        ? 'border-red-500 focus:border-red-500 focus:ring-red-500'
                        : 'border-gray-700 focus:border-neon-cyan focus:ring-neon-cyan'
                    }`}
                    placeholder="{}"
                  />
                  {jsonError && (
                    <p className="mt-1 text-sm text-red-400">{jsonError}</p>
                  )}
                </div>
              ) : (
                <div className="space-y-4">
                  {interfaceType === 'rest_api' && (
                    <RestApiConfigEditor
                      config={(interfaceConfig as RestApiConfig) || getDefaultConfig('rest_api')}
                      onChange={onConfigChange}
                    />
                  )}
                  {interfaceType === 'cli' && (
                    <CliConfigEditor
                      config={(interfaceConfig as CliConfig) || getDefaultConfig('cli')}
                      onChange={onConfigChange}
                    />
                  )}
                  {interfaceType === 'python_sdk' && (
                    <PythonSdkConfigEditor
                      config={(interfaceConfig as PythonSdkConfig) || getDefaultConfig('python_sdk')}
                      onChange={onConfigChange}
                    />
                  )}
                  {interfaceType === 'mcp' && (
                    <McpConfigEditor
                      config={(interfaceConfig as McpConfig) || getDefaultConfig('mcp')}
                      onChange={onConfigChange}
                    />
                  )}
                </div>
              )}

              {/* Input/Output Schema */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Input Schema (JSON)
                  </label>
                  <textarea
                    value={inputSchema ? JSON.stringify(inputSchema, null, 2) : ''}
                    onChange={(e) => {
                      try {
                        const parsed = e.target.value ? JSON.parse(e.target.value) : null;
                        onInputSchemaChange(parsed);
                      } catch {
                        // Allow invalid JSON while typing
                      }
                    }}
                    rows={6}
                    className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    placeholder='{"type": "object", "properties": {}}'
                  />
                  <p className="mt-1 text-xs text-gray-500">JSON Schema for input validation</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Output Schema (JSON)
                  </label>
                  <textarea
                    value={outputSchema ? JSON.stringify(outputSchema, null, 2) : ''}
                    onChange={(e) => {
                      try {
                        const parsed = e.target.value ? JSON.parse(e.target.value) : null;
                        onOutputSchemaChange(parsed);
                      } catch {
                        // Allow invalid JSON while typing
                      }
                    }}
                    rows={6}
                    className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    placeholder='{"type": "object", "properties": {}}'
                  />
                  <p className="mt-1 text-xs text-gray-500">JSON Schema for output structure</p>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// REST API Config Editor
function RestApiConfigEditor({ config, onChange }: { config: RestApiConfig; onChange: (c: RestApiConfig) => void }) {
  const [newEndpointKey, setNewEndpointKey] = useState('');

  const addEndpoint = () => {
    if (newEndpointKey && !config.endpoints[newEndpointKey]) {
      onChange({
        ...config,
        endpoints: {
          ...config.endpoints,
          [newEndpointKey]: { method: 'GET', path: '/' }
        }
      });
      setNewEndpointKey('');
    }
  };

  const removeEndpoint = (key: string) => {
    const { [key]: _, ...rest } = config.endpoints;
    onChange({ ...config, endpoints: rest });
  };

  const updateEndpoint = (key: string, field: string, value: string) => {
    onChange({
      ...config,
      endpoints: {
        ...config.endpoints,
        [key]: { ...config.endpoints[key], [field]: value }
      }
    });
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Base URL</label>
        <input
          type="text"
          value={config.base_url}
          onChange={(e) => onChange({ ...config, base_url: e.target.value })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder="https://api.example.com"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Authentication</label>
        <select
          value={config.auth_type || 'none'}
          onChange={(e) => onChange({ ...config, auth_type: e.target.value as any })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
        >
          <option value="none">None</option>
          <option value="bearer">Bearer Token</option>
          <option value="api_key">API Key</option>
          <option value="basic">Basic Auth</option>
        </select>
      </div>

      {config.auth_type && config.auth_type !== 'none' && (
        <div className="pl-4 border-l-2 border-gray-700 space-y-2">
          {config.auth_type === 'bearer' && (
            <div>
              <label className="block text-sm text-gray-400 mb-1">Token (use $ENV_VAR for secrets)</label>
              <input
                type="text"
                value={config.auth_config?.token || ''}
                onChange={(e) => onChange({ ...config, auth_config: { ...config.auth_config, token: e.target.value } })}
                className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                placeholder="$API_TOKEN"
              />
            </div>
          )}
          {config.auth_type === 'api_key' && (
            <>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Header Name</label>
                <input
                  type="text"
                  value={config.auth_config?.header || ''}
                  onChange={(e) => onChange({ ...config, auth_config: { ...config.auth_config, header: e.target.value } })}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="X-API-Key"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Key Value</label>
                <input
                  type="text"
                  value={config.auth_config?.key || ''}
                  onChange={(e) => onChange({ ...config, auth_config: { ...config.auth_config, key: e.target.value } })}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="$API_KEY"
                />
              </div>
            </>
          )}
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Endpoints</label>
        <div className="space-y-3">
          {Object.entries(config.endpoints).map(([key, endpoint]) => (
            <div key={key} className="flex items-start gap-2 p-3 bg-gray-800/50 rounded-lg">
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-neon-cyan font-mono">{key}</span>
                </div>
                <div className="flex gap-2">
                  <select
                    value={endpoint.method}
                    onChange={(e) => updateEndpoint(key, 'method', e.target.value)}
                    className="px-3 py-1 bg-gray-900/50 border border-gray-700 rounded text-sm text-white"
                  >
                    <option>GET</option>
                    <option>POST</option>
                    <option>PUT</option>
                    <option>PATCH</option>
                    <option>DELETE</option>
                  </select>
                  <input
                    type="text"
                    value={endpoint.path}
                    onChange={(e) => updateEndpoint(key, 'path', e.target.value)}
                    className="flex-1 px-3 py-1 bg-gray-900/50 border border-gray-700 rounded text-sm text-white"
                    placeholder="/path"
                  />
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeEndpoint(key)}
                className="p-1 text-gray-500 hover:text-red-400"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              type="text"
              value={newEndpointKey}
              onChange={(e) => setNewEndpointKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addEndpoint())}
              className="flex-1 px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white"
              placeholder="Endpoint name (e.g., search, create)"
            />
            <button
              type="button"
              onClick={addEndpoint}
              className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300 hover:border-neon-cyan"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// CLI Config Editor
function CliConfigEditor({ config, onChange }: { config: CliConfig; onChange: (c: CliConfig) => void }) {
  const [newTemplateKey, setNewTemplateKey] = useState('');

  const addTemplate = () => {
    if (newTemplateKey && !config.templates[newTemplateKey]) {
      onChange({
        ...config,
        templates: {
          ...config.templates,
          [newTemplateKey]: { args: [], env: {} }
        }
      });
      setNewTemplateKey('');
    }
  };

  const removeTemplate = (key: string) => {
    const { [key]: _, ...rest } = config.templates;
    onChange({ ...config, templates: rest });
  };

  const updateTemplateArgs = (key: string, argsStr: string) => {
    // Parse comma-separated or space-separated args, respecting quoted strings
    const args = argsStr.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
    onChange({
      ...config,
      templates: {
        ...config.templates,
        [key]: { ...config.templates[key], args }
      }
    });
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Command</label>
        <input
          type="text"
          value={config.command}
          onChange={(e) => onChange({ ...config, command: e.target.value })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder="ffmpeg"
        />
        <p className="mt-1 text-xs text-gray-500">The base command to execute</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Working Directory</label>
        <input
          type="text"
          value={config.working_dir || ''}
          onChange={(e) => onChange({ ...config, working_dir: e.target.value || undefined })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder="/tmp/tool_workspace"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Command Templates</label>
        <p className="text-xs text-gray-500 mb-3">
          Use {'{{param}}'} placeholders in args for dynamic values
        </p>
        <div className="space-y-3">
          {Object.entries(config.templates).map(([key, template]) => (
            <div key={key} className="p-3 bg-gray-800/50 rounded-lg space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm text-neon-cyan font-mono">{key}</span>
                <button
                  type="button"
                  onClick={() => removeTemplate(key)}
                  className="p-1 text-gray-500 hover:text-red-400"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Arguments</label>
                <input
                  type="text"
                  value={template.args.join(' ')}
                  onChange={(e) => updateTemplateArgs(key, e.target.value)}
                  className="w-full px-3 py-1 bg-gray-900/50 border border-gray-700 rounded text-sm text-white font-mono"
                  placeholder="-i {{input}} -o {{output}}"
                />
              </div>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              type="text"
              value={newTemplateKey}
              onChange={(e) => setNewTemplateKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTemplate())}
              className="flex-1 px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white"
              placeholder="Template name (e.g., convert, encode)"
            />
            <button
              type="button"
              onClick={addTemplate}
              className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300 hover:border-neon-cyan"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Python SDK Config Editor
function PythonSdkConfigEditor({ config, onChange }: { config: PythonSdkConfig; onChange: (c: PythonSdkConfig) => void }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">Module</label>
          <input
            type="text"
            value={config.module}
            onChange={(e) => onChange({ ...config, module: e.target.value })}
            className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            placeholder="openai"
          />
          <p className="mt-1 text-xs text-gray-500">Python module to import</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">Class (optional)</label>
          <input
            type="text"
            value={config.class || ''}
            onChange={(e) => onChange({ ...config, class: e.target.value || undefined })}
            className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            placeholder="OpenAI"
          />
          <p className="mt-1 text-xs text-gray-500">Class to instantiate</p>
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Method</label>
        <input
          type="text"
          value={config.method}
          onChange={(e) => onChange({ ...config, method: e.target.value })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder="chat.completions.create"
        />
        <p className="mt-1 text-xs text-gray-500">Method to call (supports nested like obj.method.sub)</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Init Arguments (JSON)</label>
        <textarea
          value={config.init_args ? JSON.stringify(config.init_args, null, 2) : '{}'}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value);
              onChange({ ...config, init_args: parsed });
            } catch {
              // Allow invalid JSON while typing
            }
          }}
          rows={3}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder='{"api_key": "$OPENAI_API_KEY"}'
        />
        <p className="mt-1 text-xs text-gray-500">Use $ENV_VAR for environment variables</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Method Kwargs Mapping (JSON)</label>
        <textarea
          value={config.method_kwargs_mapping ? JSON.stringify(config.method_kwargs_mapping, null, 2) : '{}'}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value);
              onChange({ ...config, method_kwargs_mapping: parsed });
            } catch {
              // Allow invalid JSON while typing
            }
          }}
          rows={3}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder='{"model": "$.model", "messages": "$.messages"}'
        />
        <p className="mt-1 text-xs text-gray-500">Map input params to method kwargs using JSONPath</p>
      </div>
    </div>
  );
}

// MCP Config Editor
function McpConfigEditor({ config, onChange }: { config: McpConfig; onChange: (c: McpConfig) => void }) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Transport</label>
        <div className="flex gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              checked={config.transport === 'stdio'}
              onChange={() => onChange({ ...config, transport: 'stdio', server_url: undefined })}
              className="text-neon-cyan focus:ring-neon-cyan"
            />
            <span className="text-gray-300">STDIO (spawn process)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              checked={config.transport === 'http'}
              onChange={() => onChange({ ...config, transport: 'http', server_command: undefined })}
              className="text-neon-cyan focus:ring-neon-cyan"
            />
            <span className="text-gray-300">HTTP (connect to server)</span>
          </label>
        </div>
      </div>

      {config.transport === 'stdio' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">Server Command</label>
          <input
            type="text"
            value={(config.server_command || []).join(' ')}
            onChange={(e) => {
              const parts = e.target.value.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
              onChange({ ...config, server_command: parts });
            }}
            className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            placeholder="npx -y @modelcontextprotocol/server-filesystem /path"
          />
          <p className="mt-1 text-xs text-gray-500">Command to spawn the MCP server</p>
        </div>
      )}

      {config.transport === 'http' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">Server URL</label>
          <input
            type="text"
            value={config.server_url || ''}
            onChange={(e) => onChange({ ...config, server_url: e.target.value })}
            className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            placeholder="http://localhost:8080"
          />
          <p className="mt-1 text-xs text-gray-500">URL of the running MCP server</p>
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Tool Name</label>
        <input
          type="text"
          value={config.tool_name}
          onChange={(e) => onChange({ ...config, tool_name: e.target.value })}
          className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white font-mono focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          placeholder="read_file"
        />
        <p className="mt-1 text-xs text-gray-500">The MCP tool name to invoke</p>
      </div>
    </div>
  );
}
