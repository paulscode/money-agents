/**
 * Parser for tool edit suggestions from agent responses.
 * 
 * The agent uses XML-style tags to suggest edits:
 * <tool_edit field="name">New Tool Name</tool_edit>
 */

export interface ToolEdit {
  field: string;
  value: string;
  originalTag: string; // Full tag for display/removal
}

// Fields that can be edited and their display names
export const TOOL_EDITABLE_FIELDS: Record<string, { label: string; type: 'string' | 'number' | 'text' | 'select' | 'json' | 'date' }> = {
  // Basic Information
  name: { label: 'Name', type: 'string' },
  slug: { label: 'Slug', type: 'string' },
  category: { label: 'Category', type: 'select' },
  description: { label: 'Description', type: 'text' },
  tags: { label: 'Tags', type: 'json' },
  
  // Implementation
  implementation_notes: { label: 'Implementation Notes', type: 'text' },
  blockers: { label: 'Blockers', type: 'text' },
  dependencies: { label: 'Dependencies', type: 'json' },
  estimated_completion_date: { label: 'Est. Completion Date', type: 'date' },
  
  // Usage & Integration
  usage_instructions: { label: 'Usage Instructions', type: 'text' },
  example_code: { label: 'Example Code', type: 'text' },
  required_environment_variables: { label: 'Required Env Vars', type: 'json' },
  integration_complexity: { label: 'Integration Complexity', type: 'select' },
  
  // Resources & Costs
  cost_model: { label: 'Cost Model', type: 'string' },
  cost_details: { label: 'Cost Details', type: 'json' },
  resource_ids: { label: 'Resource IDs', type: 'json' },
  
  // Documentation
  strengths: { label: 'Strengths', type: 'text' },
  weaknesses: { label: 'Weaknesses', type: 'text' },
  best_use_cases: { label: 'Best Use Cases', type: 'text' },
  external_documentation_url: { label: 'External Docs URL', type: 'string' },
  
  // Metadata
  version: { label: 'Version', type: 'string' },
  priority: { label: 'Priority', type: 'select' },
  status: { label: 'Status', type: 'select' },
};

// Valid values for select fields
export const TOOL_SELECT_OPTIONS: Record<string, string[]> = {
  category: ['api', 'data_source', 'automation', 'analysis', 'communication'],
  integration_complexity: ['low', 'medium', 'high'],
  priority: ['low', 'medium', 'high', 'critical'],
  status: [
    'requested', 'under_review', 'changes_requested', 'approved', 'rejected',
    'implementing', 'testing', 'blocked', 'on_hold',
    'implemented', 'deprecated', 'retired'
  ],
};

/**
 * Decode XML entities to their original characters.
 */
function decodeXmlEntities(str: string): string {
  return str
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'");
}

/**
 * Extract content from CDATA section if present, otherwise return as-is.
 */
function extractCDATA(str: string): string {
  // Match CDATA: <![CDATA[...]]>
  const cdataMatch = str.match(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/);
  if (cdataMatch) {
    return cdataMatch[1]; // Content inside CDATA is literal, no decoding needed
  }
  // No CDATA - decode XML entities
  return decodeXmlEntities(str);
}

/**
 * Parse agent response for tool edit tags.
 * Returns array of edits found.
 * 
 * Handles:
 * - CDATA sections for content with special characters
 * - XML entity escaping (&lt; &gt; &amp; etc.)
 * - Multiline content
 */
export function parseToolEdits(content: string): ToolEdit[] {
  const edits: ToolEdit[] = [];
  
  // Match <tool_edit field="...">...</tool_edit>
  const editRegex = /<tool_edit\s+field=["']([^"']+)["']>([\s\S]*?)<\/tool_edit>/gi;
  
  let match;
  while ((match = editRegex.exec(content)) !== null) {
    const field = match[1].toLowerCase().trim();
    const rawValue = match[2].trim();
    const value = extractCDATA(rawValue);
    const originalTag = match[0];
    
    // Only include valid fields
    if (TOOL_EDITABLE_FIELDS[field]) {
      // Validate select options
      if (TOOL_SELECT_OPTIONS[field]) {
        if (!TOOL_SELECT_OPTIONS[field].includes(value.toLowerCase())) {
          continue; // Skip invalid select value
        }
      }
      edits.push({ field, value, originalTag });
    }
  }
  
  return edits;
}

/**
 * Remove tool edit tags from content for clean display.
 */
export function removeToolEditTags(content: string): string {
  return content.replace(
    /<tool_edit\s+field=["']([^"']+)["']>([\s\S]*?)<\/tool_edit>/gi,
    ''
  );
}

/**
 * Check if content has any tool edits.
 */
export function hasToolEdits(content: string): boolean {
  return /<tool_edit\s+field=/i.test(content);
}

/**
 * Format a field value for display.
 */
export function formatToolFieldValue(field: string, value: string): string {
  const fieldInfo = TOOL_EDITABLE_FIELDS[field];
  if (!fieldInfo) return value;
  
  // Format based on type
  switch (fieldInfo.type) {
    case 'text':
      // Truncate long text for display
      return value.length > 100 ? value.substring(0, 100) + '...' : value;
    case 'select':
      // Capitalize select values
      return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ');
    default:
      return value;
  }
}

/**
 * Convert edit value to appropriate type for API.
 */
export function convertToolEditValue(field: string, value: string): string | number {
  const fieldInfo = TOOL_EDITABLE_FIELDS[field];
  if (!fieldInfo) return value;
  
  // All tool fields are strings
  return value;
}
