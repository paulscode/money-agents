/**
 * Tests for the SanitizedMarkdown component (SA-04/SA-11).
 *
 * Verifies that DOMPurify sanitization is applied to markdown content
 * and dangerous HTML/scripts are stripped before rendering.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DOMPurify from 'dompurify';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';

// Mock MDEditor to capture what gets passed through
vi.mock('@uiw/react-md-editor', () => ({
  default: {
    Markdown: ({ source, ...props }: { source: string }) => (
      <div data-testid="md-output" data-source={source} {...props} />
    ),
  },
}));

describe('SanitizedMarkdown', () => {
  it('renders without crashing', () => {
    render(<SanitizedMarkdown source="Hello, world!" />);
    expect(screen.getByTestId('md-output')).toBeDefined();
  });

  it('passes sanitized content to MDEditor.Markdown', () => {
    render(<SanitizedMarkdown source="**bold text**" />);
    const output = screen.getByTestId('md-output');
    expect(output.getAttribute('data-source')).toBe('**bold text**');
  });

  it('strips script tags from source', () => {
    const malicious = 'Hello <script>alert("xss")</script> world';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('<script>');
    expect(sanitized).not.toContain('alert');
  });

  it('strips on* event handlers from tags', () => {
    const malicious = '<img src="x" onerror="alert(1)" />';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('onerror');
    expect(sanitized).not.toContain('alert');
  });

  it('strips javascript: URIs from href', () => {
    const malicious = '<a href="javascript:alert(1)">click me</a>';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('javascript:');
  });

  it('preserves safe HTML tags', () => {
    const safe = '<strong>bold</strong> and <em>italic</em>';
    render(<SanitizedMarkdown source={safe} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).toContain('<strong>');
    expect(sanitized).toContain('<em>');
  });

  it('preserves safe links with https:', () => {
    const safe = '<a href="https://example.com">link</a>';
    render(<SanitizedMarkdown source={safe} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).toContain('href="https://example.com"');
  });

  it('handles undefined source gracefully', () => {
    render(<SanitizedMarkdown source={undefined} />);
    const output = screen.getByTestId('md-output');
    expect(output.getAttribute('data-source')).toBe('');
  });

  it('handles empty string source', () => {
    render(<SanitizedMarkdown source="" />);
    const output = screen.getByTestId('md-output');
    expect(output.getAttribute('data-source')).toBe('');
  });

  it('wraps in prose container when wrapInProse is true', () => {
    const { container } = render(
      <SanitizedMarkdown source="Hello" wrapInProse />
    );
    const proseDiv = container.querySelector('.prose');
    expect(proseDiv).toBeDefined();
    expect(proseDiv?.getAttribute('data-color-mode')).toBe('dark');
  });

  it('strips iframe tags', () => {
    const malicious = '<iframe src="https://evil.com"></iframe>';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('<iframe');
  });

  it('strips data: URIs', () => {
    const malicious = '<a href="data:text/html,<script>alert(1)</script>">x</a>';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('data:text/html');
  });

  it('strips onclick attributes', () => {
    const malicious = '<div onclick="alert(1)">click</div>';
    render(<SanitizedMarkdown source={malicious} />);
    const output = screen.getByTestId('md-output');
    const sanitized = output.getAttribute('data-source') || '';
    expect(sanitized).not.toContain('onclick');
  });
});
