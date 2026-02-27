import apiClient from '../lib/api-client';
import type {
  ToolMediaSummary,
  MediaFileList,
  MediaStats,
} from '../types';

const BASE_PATH = '/api/v1/media';

/**
 * Fetch a media endpoint as a blob and return an object URL.
 * Used because <img>/<video>/<audio> tags can't send JWT auth headers.
 */
async function fetchBlobUrl(path: string): Promise<string> {
  const response = await apiClient.get(path, { responseType: 'blob' });
  // Re-wrap with explicit MIME type from response headers — axios blob
  // responses don't always preserve the Content-Type on the Blob object,
  // which causes <video>/<audio> elements to show duration 0.
  const contentType = response.headers['content-type'] || 'application/octet-stream';
  const blob = new Blob([response.data], { type: contentType });
  return URL.createObjectURL(blob);
}

/**
 * Trigger an authenticated file download via fetch + programmatic click.
 */
async function triggerAuthDownload(path: string, filename: string): Promise<void> {
  const blobUrl = await fetchBlobUrl(path);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(blobUrl);
}

export const mediaLibraryService = {
  /**
   * List all tools with media output files
   */
  async listToolsWithMedia(): Promise<ToolMediaSummary[]> {
    const response = await apiClient.get(`${BASE_PATH}/tools`);
    return response.data;
  },

  /**
   * Get global media library statistics
   */
  async getStats(): Promise<MediaStats> {
    const response = await apiClient.get(`${BASE_PATH}/stats`);
    return response.data;
  },

  /**
   * List files for a specific tool
   */
  async listFiles(
    toolSlug: string,
    params?: {
      page?: number;
      page_size?: number;
      sort_by?: 'modified_at' | 'name' | 'size';
      sort_order?: 'asc' | 'desc';
      media_type?: string;
    }
  ): Promise<MediaFileList> {
    const response = await apiClient.get(`${BASE_PATH}/${toolSlug}/files`, {
      params,
    });
    return response.data;
  },

  /**
   * Get the URL for downloading/viewing a file
   */
  getFileUrl(toolSlug: string, filename: string, download = false): string {
    const base = `${BASE_PATH}/${toolSlug}/files/${encodeURIComponent(filename)}`;
    return download ? `${base}?download=true` : base;
  },

  /**
   * Get the URL for a file's thumbnail
   */
  getThumbnailUrl(toolSlug: string, filename: string): string {
    return `${BASE_PATH}/${toolSlug}/files/${encodeURIComponent(filename)}/thumbnail`;
  },

  /**
   * Trigger batch thumbnail generation for a tool
   */
  async generateThumbnails(
    toolSlug: string
  ): Promise<{ generated: number; errors: number; remaining: number }> {
    const response = await apiClient.post(
      `${BASE_PATH}/${toolSlug}/thumbnails/generate`
    );
    return response.data;
  },

  /**
   * Delete a file (admin only)
   */
  async deleteFile(toolSlug: string, filename: string): Promise<void> {
    await apiClient.delete(
      `${BASE_PATH}/${toolSlug}/files/${encodeURIComponent(filename)}`
    );
  },

  /**
   * Fetch a file as an authenticated blob URL (for <img>, <video>, <audio>)
   */
  async fetchFileBlobUrl(toolSlug: string, filename: string): Promise<string> {
    return fetchBlobUrl(this.getFileUrl(toolSlug, filename));
  },

  /**
   * Fetch a thumbnail as an authenticated blob URL
   */
  async fetchThumbnailBlobUrl(toolSlug: string, filename: string): Promise<string> {
    return fetchBlobUrl(this.getThumbnailUrl(toolSlug, filename));
  },

  /**
   * Download a file with authentication (programmatic)
   */
  async downloadFile(toolSlug: string, filename: string): Promise<void> {
    return triggerAuthDownload(this.getFileUrl(toolSlug, filename, true), filename);
  },
};
