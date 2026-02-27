/**
 * Parser for proposal edit suggestions from agent responses.
 * 
 * The agent uses XML-style tags to suggest edits:
 * <proposal_edit field="title">New Title Here</proposal_edit>
 */

export interface ProposalEdit {
  field: string;
  value: string;
  originalTag: string; // Full tag for display/removal
}

// Fields that can be edited and their display names
export const EDITABLE_FIELDS: Record<string, { label: string; type: 'string' | 'number' | 'text' }> = {
  title: { label: 'Title', type: 'string' },
  summary: { label: 'Summary', type: 'string' },
  detailed_description: { label: 'Detailed Description', type: 'text' },
  initial_budget: { label: 'Initial Budget', type: 'number' },
  bitcoin_budget_sats: { label: 'Bitcoin Budget (sats)', type: 'number' },
  bitcoin_budget_rationale: { label: 'Bitcoin Budget Rationale', type: 'text' },
  risk_level: { label: 'Risk Level', type: 'string' },
  risk_description: { label: 'Risk Description', type: 'text' },
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
 * Parse agent response for proposal edit tags.
 * Returns array of edits found.
 * 
 * Handles:
 * - CDATA sections for content with special characters
 * - XML entity escaping (&lt; &gt; &amp; etc.)
 * - Multiline content
 */
export function parseProposalEdits(content: string): ProposalEdit[] {
  const edits: ProposalEdit[] = [];
  
  // Match <proposal_edit field="...">...</proposal_edit>
  // Using a regex that handles multiline content
  // The content can be either CDATA or regular (possibly escaped) text
  const editRegex = /<proposal_edit\s+field=["']([^"']+)["']>([\s\S]*?)<\/proposal_edit>/gi;
  
  let match;
  while ((match = editRegex.exec(content)) !== null) {
    const field = match[1].toLowerCase().trim();
    const rawValue = match[2].trim();
    const value = extractCDATA(rawValue); // Handle CDATA or decode entities
    const originalTag = match[0];
    
    // Only include valid fields
    if (EDITABLE_FIELDS[field]) {
      edits.push({ field, value, originalTag });
    }
  }
  
  return edits;
}

/**
 * Remove proposal edit tags from content for clean display.
 * Replaces tags with a placeholder that can be styled.
 */
export function removeEditTags(content: string): string {
  return content.replace(
    /<proposal_edit\s+field=["']([^"']+)["']>([\s\S]*?)<\/proposal_edit>/gi,
    '' // Remove entirely - we'll show edits separately
  );
}

/**
 * Check if content has any proposal edits.
 */
export function hasProposalEdits(content: string): boolean {
  return /<proposal_edit\s+field=/i.test(content);
}

/**
 * Format a field value for display based on its type.
 */
export function formatFieldValue(field: string, value: string): string {
  const fieldInfo = EDITABLE_FIELDS[field];
  if (!fieldInfo) return value;
  
  if (fieldInfo.type === 'number') {
    const num = parseFloat(value);
    if (!isNaN(num)) {
      if (field === 'bitcoin_budget_sats') {
        return `${Math.round(num).toLocaleString()} sats`;
      }
      return `$${num.toLocaleString()}`;
    }
  }
  
  // Truncate long text for preview
  if (fieldInfo.type === 'text' && value.length > 100) {
    return value.substring(0, 100) + '...';
  }
  
  return value;
}

/**
 * Convert edit value to the proper type for API submission.
 */
export function convertEditValue(field: string, value: string): string | number {
  const fieldInfo = EDITABLE_FIELDS[field];
  if (!fieldInfo) return value;
  
  if (fieldInfo.type === 'number') {
    // Remove any $ or commas, parse as number
    const cleaned = value.replace(/[$,]/g, '');
    const num = parseFloat(cleaned);
    return isNaN(num) ? 0 : num;
  }
  
  return value;
}
