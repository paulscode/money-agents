import apiClient from '../lib/api-client';
import type { Resource, ResourceCreate, Job, StorageResourceCreate, StorageInfo, StorageFile } from '../types';

export const resourcesService = {
  /**
   * Detect and initialize system resources (CPU, RAM, GPU, Storage)
   */
  async detectResources(): Promise<{ message: string; created: number; updated: number; types: string[] }> {
    const response = await apiClient.post('/api/v1/resources/detect');
    return response.data;
  },

  /**
   * @deprecated Use detectResources() instead
   */
  async initializeGPUs(): Promise<{ message: string; count: number }> {
    const response = await apiClient.post('/api/v1/resources/initialize-gpus');
    return response.data;
  },

  /**
   * Get all resources
   */
  async getAll(): Promise<Resource[]> {
    const response = await apiClient.get('/api/v1/resources');
    return response.data;
  },

  /**
   * Get a specific resource
   */
  async getById(id: string): Promise<Resource> {
    const response = await apiClient.get(`/api/v1/resources/${id}`);
    return response.data;
  },

  /**
   * Create a new custom resource
   */
  async create(data: ResourceCreate): Promise<Resource> {
    const response = await apiClient.post('/api/v1/resources', data);
    return response.data;
  },

  /**
   * Create a new storage resource
   */
  async createStorage(data: StorageResourceCreate): Promise<Resource> {
    const response = await apiClient.post('/api/v1/resources/storage', data);
    return response.data;
  },

  /**
   * Update resource status
   */
  async updateStatus(id: string, status: string): Promise<Resource> {
    const response = await apiClient.patch(`/api/v1/resources/${id}/status`, { status });
    return response.data;
  },

  /**
   * Delete a resource (custom resources only)
   */
  async delete(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/resources/${id}`);
  },

  /**
   * Get job queue for a resource
   */
  async getQueue(id: string): Promise<Job[]> {
    const response = await apiClient.get(`/api/v1/resources/${id}/queue`);
    return response.data;
  },

  // =========================================================================
  // Storage-specific endpoints
  // =========================================================================

  /**
   * Get detailed storage info for a storage resource
   */
  async getStorageInfo(id: string): Promise<StorageInfo> {
    const response = await apiClient.get(`/api/v1/resources/${id}/storage`);
    return response.data;
  },

  /**
   * Scan/refresh storage space info
   */
  async scanStorage(id: string): Promise<{ message: string; total_bytes: number; used_bytes: number; available_bytes: number }> {
    const response = await apiClient.post(`/api/v1/resources/${id}/storage/scan`);
    return response.data;
  },

  /**
   * Get tracked files for a storage resource
   */
  async getStorageFiles(id: string): Promise<StorageFile[]> {
    const response = await apiClient.get(`/api/v1/resources/${id}/storage/files`);
    return response.data;
  },

  /**
   * Find cleanable files on a storage resource
   */
  async getCleanableFiles(id: string, olderThanDays: number = 30, temporaryOnly: boolean = false): Promise<StorageFile[]> {
    const response = await apiClient.get(`/api/v1/resources/${id}/storage/cleanable`, {
      params: { older_than_days: olderThanDays, temporary_only: temporaryOnly }
    });
    return response.data;
  },

  // =========================================================================
  // Test endpoints for simulating load
  // =========================================================================

  async simulateLoad(resourceId: string, numJobs: number = 5, jobDuration: number = 10): Promise<any> {
    const response = await apiClient.post('/api/v1/test/resources/simulate-load', null, {
      params: { resource_id: resourceId, num_jobs: numJobs, job_duration: jobDuration }
    });
    return response.data;
  },

  async completeTestJob(jobId: string, success: boolean = true): Promise<any> {
    const response = await apiClient.post(`/api/v1/test/resources/complete-job/${jobId}`, null, {
      params: { success }
    });
    return response.data;
  },

  async clearTestJobs(resourceId: string): Promise<any> {
    const response = await apiClient.delete(`/api/v1/test/resources/clear-test-jobs/${resourceId}`);
    return response.data;
  },
};
