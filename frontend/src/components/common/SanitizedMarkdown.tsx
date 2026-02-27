/**
 * RT-30: Sanitized Markdown renderer.
 *
 * Wraps @uiw/react-md-editor's Markdown component with DOMPurify sanitization
 * to prevent XSS from user/agent-supplied markdown content.
 */
import React, { useMemo } from 'react';
import MDEditor from '@uiw/react-md-editor';
import DOMPurify from 'dompurify';

interface SanitizedMarkdownProps {
  source: string | undefined;
  style?: React.CSSProperties;
  className?: string;
  /** Wrap in a prose container with dark color mode */
  wrapInProse?: boolean;
}

const DEFAULT_STYLE: React.CSSProperties = {
  backgroundColor: 'transparent',
  color: '#e5e7eb',
};

/**
 * Renders Markdown content after sanitizing it with DOMPurify.
 *
 * Strips dangerous HTML (script, iframe, event handlers, etc.) while
 * preserving safe formatting tags.
 */
export const SanitizedMarkdown: React.FC<SanitizedMarkdownProps> = ({
  source,
  style,
  className,
  wrapInProse = false,
}) => {
  // Sanitize source Markdown before rendering.
  // DOMPurify strips <script>, on* attributes, javascript: hrefs, etc.
  const sanitized = useMemo(() => {
    if (!source) return '';
    return DOMPurify.sanitize(source, {
      // Allow safe HTML that Markdown might generate
      ALLOWED_TAGS: [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'br', 'hr',
        'ul', 'ol', 'li',
        'strong', 'em', 'b', 'i', 'u', 's', 'del', 'ins',
        'a', 'img',
        'blockquote', 'pre', 'code',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span', 'sup', 'sub',
        'details', 'summary',
        'input', // for checkboxes in task lists
      ],
      ALLOWED_ATTR: [
        'href', 'src', 'alt', 'title', 'class', 'id',
        'target', 'rel',
        'type', 'checked', 'disabled', // checkbox attrs
        'align', 'width', 'height',
      ],
      // Strip javascript: and data: URIs
      ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
    });
  }, [source]);

  const content = (
    <MDEditor.Markdown
      source={sanitized}
      style={{ ...DEFAULT_STYLE, ...style }}
      className={className}
    />
  );

  if (wrapInProse) {
    return (
      <div data-color-mode="dark" className="prose prose-invert prose-sm max-w-none">
        {content}
      </div>
    );
  }

  return content;
};

export default SanitizedMarkdown;
