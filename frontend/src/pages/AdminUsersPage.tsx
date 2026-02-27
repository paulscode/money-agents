import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, UserCheck, UserX, Users, Clock, CheckCircle, XCircle, Trash2, RotateCcw, KeyRound, Copy, Check } from 'lucide-react';
import { adminService } from '@/services/admin';
import { useAuthStore } from '@/stores/auth';
import { Layout } from '@/components/layout/Layout';
import type { User, ResetCodeResponse } from '@/types';

export function AdminUsersPage() {
  const queryClient = useQueryClient();
  const { user: currentUser } = useAuthStore();
  const [activeTab, setActiveTab] = useState<'pending' | 'all'>('pending');
  const [deleteConfirm, setDeleteConfirm] = useState<{ userId: string; username: string } | null>(null);
  const [resetCodeResult, setResetCodeResult] = useState<ResetCodeResponse | null>(null);
  const [codeCopied, setCodeCopied] = useState(false);

  const { data: pendingUsers = [], isLoading: loadingPending } = useQuery({
    queryKey: ['admin', 'users', 'pending'],
    queryFn: () => adminService.getPendingUsers(),
  });

  const { data: allUsers = [], isLoading: loadingAll } = useQuery({
    queryKey: ['admin', 'users', 'all'],
    queryFn: () => adminService.getAllUsers(),
    enabled: activeTab === 'all',
  });

  const approveMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: 'user' | 'admin' }) =>
      adminService.approveUser(userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (userId: string) => adminService.rejectUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (userId: string) => adminService.deactivateUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const reactivateMutation = useMutation({
    mutationFn: (userId: string) => adminService.reactivateUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => adminService.deleteUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
      setDeleteConfirm(null);
    },
  });

  const updateRoleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: 'user' | 'admin' }) =>
      adminService.updateUserRole(userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const generateResetCodeMutation = useMutation({
    mutationFn: (userId: string) => adminService.generateResetCode(userId),
    onSuccess: (data) => {
      setResetCodeResult(data);
      setCodeCopied(false);
    },
  });

  const getRoleBadgeClass = (role: string) => {
    switch (role) {
      case 'admin':
        return 'bg-neon-purple/20 text-neon-purple border-neon-purple/30';
      case 'user':
        return 'bg-neon-cyan/20 text-neon-cyan border-neon-cyan/30';
      case 'pending':
        return 'bg-neon-yellow/20 text-neon-yellow border-neon-yellow/30';
      default:
        return 'bg-gray-700/20 text-gray-400 border-gray-600/30';
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const isSystemUser = (user: User) => {
    return user.username === 'system' || user.email === 'system@money-agents.dev';
  };

  const users = activeTab === 'pending' ? pendingUsers : allUsers;
  const isLoading = activeTab === 'pending' ? loadingPending : loadingAll;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-neon-cyan mb-2">User Management</h1>
          <p className="text-gray-400">Approve new users and manage existing accounts</p>
        </div>
        <Shield className="h-12 w-12 text-neon-cyan opacity-50" />
      </div>

      {/* Tabs */}
      <div className="flex space-x-2 border-b border-gray-700">
        <button
          onClick={() => setActiveTab('pending')}
          className={`px-6 py-3 font-medium transition-colors relative ${
            activeTab === 'pending'
              ? 'text-neon-cyan'
              : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          <div className="flex items-center space-x-2">
            <Clock className="h-5 w-5" />
            <span>Pending Approval</span>
            {pendingUsers.length > 0 && (
              <span className="px-2 py-0.5 text-xs bg-neon-yellow/20 text-neon-yellow rounded-full">
                {pendingUsers.length}
              </span>
            )}
          </div>
          {activeTab === 'pending' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-neon-cyan"></div>
          )}
        </button>
        <button
          onClick={() => setActiveTab('all')}
          className={`px-6 py-3 font-medium transition-colors relative ${
            activeTab === 'all'
              ? 'text-neon-cyan'
              : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          <div className="flex items-center space-x-2">
            <Users className="h-5 w-5" />
            <span>All Users</span>
          </div>
          {activeTab === 'all' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-neon-cyan"></div>
          )}
        </button>
      </div>

      {/* Users Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-400">Loading users...</div>
        ) : users.length === 0 ? (
          <div className="p-8 text-center text-gray-400">
            {activeTab === 'pending' ? 'No pending users' : 'No users found'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-300">User</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-300">Email</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-300">Role</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-300">Status</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-300">Registered</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-gray-300">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700/50">
                {users.map((user: User) => (
                  <tr key={user.id} className="hover:bg-gray-800/30 transition-colors">
                    <td className="px-6 py-4">
                      <div className="font-medium text-white">{user.username}</div>
                    </td>
                    <td className="px-6 py-4 text-gray-400 text-sm">{user.email}</td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getRoleBadgeClass(
                          user.role
                        )}`}
                      >
                        {user.role}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      {user.is_active ? (
                        <span className="inline-flex items-center text-green-400 text-sm">
                          <CheckCircle className="h-4 w-4 mr-1" />
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center text-gray-500 text-sm">
                          <XCircle className="h-4 w-4 mr-1" />
                          Inactive
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-gray-400 text-sm">
                      {formatDate(user.created_at)}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center justify-end space-x-2">
                        {user.role === 'pending' ? (
                          <>
                            <button
                              onClick={() =>
                                approveMutation.mutate({ userId: user.id, role: 'user' })
                              }
                              disabled={approveMutation.isPending}
                              className="btn-sm bg-neon-cyan/10 hover:bg-neon-cyan/20 text-neon-cyan border-neon-cyan/30"
                              title="Approve as User"
                            >
                              <UserCheck className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() =>
                                approveMutation.mutate({ userId: user.id, role: 'admin' })
                              }
                              disabled={approveMutation.isPending}
                              className="btn-sm bg-neon-purple/10 hover:bg-neon-purple/20 text-neon-purple border-neon-purple/30"
                              title="Approve as Admin"
                            >
                              <Shield className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => rejectMutation.mutate(user.id)}
                              disabled={rejectMutation.isPending}
                              className="btn-sm bg-neon-pink/10 hover:bg-neon-pink/20 text-neon-pink border-neon-pink/30"
                              title="Reject"
                            >
                              <UserX className="h-4 w-4" />
                            </button>
                          </>
                        ) : (
                          <>
                            {user.is_active && user.role === 'user' && !isSystemUser(user) && (
                              <button
                                onClick={() =>
                                  updateRoleMutation.mutate({ userId: user.id, role: 'admin' })
                                }
                                disabled={updateRoleMutation.isPending}
                                className="btn-sm bg-neon-purple/10 hover:bg-neon-purple/20 text-neon-purple border-neon-purple/30 text-xs"
                                title="Promote to Admin"
                              >
                                Make Admin
                              </button>
                            )}
                            {user.role === 'admin' && user.id !== currentUser?.id && !isSystemUser(user) && (
                              <button
                                onClick={() =>
                                  updateRoleMutation.mutate({ userId: user.id, role: 'user' })
                                }
                                disabled={updateRoleMutation.isPending}
                                className="btn-sm bg-gray-700/30 hover:bg-gray-600/30 text-gray-300 border-gray-600/30 text-xs"
                                title="Demote to User"
                              >
                                Make User
                              </button>
                            )}
                            {user.is_active && user.id !== currentUser?.id && !isSystemUser(user) ? (
                              <button
                                onClick={() => deactivateMutation.mutate(user.id)}
                                disabled={deactivateMutation.isPending}
                                className="btn-sm bg-neon-yellow/10 hover:bg-neon-yellow/20 text-neon-yellow border-neon-yellow/30 text-xs"
                                title="Deactivate"
                              >
                                Deactivate
                              </button>
                            ) : !user.is_active && !isSystemUser(user) ? (
                              <button
                                onClick={() => reactivateMutation.mutate(user.id)}
                                disabled={reactivateMutation.isPending}
                                className="btn-sm bg-green-500/10 hover:bg-green-500/20 text-green-400 border-green-500/30 text-xs flex items-center space-x-1"
                                title="Reactivate"
                              >
                                <RotateCcw className="h-3 w-3" />
                                <span>Reactivate</span>
                              </button>
                            ) : null}
                            {user.id !== currentUser?.id && !isSystemUser(user) && (
                              <button
                                onClick={() => setDeleteConfirm({ userId: user.id, username: user.username })}
                                className="btn-sm bg-red-500/10 hover:bg-red-500/20 text-red-400 border-red-500/30"
                                title="Delete User"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                            {user.is_active && user.id !== currentUser?.id && !isSystemUser(user) && (
                              <button
                                onClick={() => generateResetCodeMutation.mutate(user.id)}
                                disabled={generateResetCodeMutation.isPending}
                                className="btn-sm bg-neon-blue/10 hover:bg-neon-blue/20 text-neon-blue border-neon-blue/30 text-xs flex items-center space-x-1"
                                title="Generate Password Reset Code"
                              >
                                <KeyRound className="h-3 w-3" />
                                <span>Reset PW</span>
                              </button>
                            )}
                            {isSystemUser(user) && (
                              <span className="text-xs text-gray-500 italic">System account</span>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-navy-900 border border-red-500/30 rounded-lg p-6 max-w-md w-full shadow-2xl">
            <div className="flex items-center space-x-3 mb-4">
              <div className="bg-red-500/20 p-2 rounded-lg">
                <Trash2 className="h-6 w-6 text-red-400" />
              </div>
              <h3 className="text-xl font-bold text-white">Delete User</h3>
            </div>
            <p className="text-gray-300 mb-6">
              Are you sure you want to permanently delete user <strong className="text-white">{deleteConfirm.username}</strong>? 
              This action cannot be undone and will delete all associated data.
            </p>
            <div className="flex space-x-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="flex-1 btn-secondary"
                disabled={deleteMutation.isPending}
              >
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate(deleteConfirm.userId)}
                disabled={deleteMutation.isPending}
                className="flex-1 bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30 px-4 py-2 rounded-lg font-medium transition-colors"
              >
                {deleteMutation.isPending ? 'Deleting...' : 'Delete User'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset Code Modal */}
      {resetCodeResult && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-navy-900 border border-neon-cyan/30 rounded-lg p-6 max-w-md w-full shadow-2xl">
            <div className="flex items-center space-x-3 mb-4">
              <div className="bg-neon-cyan/20 p-2 rounded-lg">
                <KeyRound className="h-6 w-6 text-neon-cyan" />
              </div>
              <h3 className="text-xl font-bold text-white">Password Reset Code</h3>
            </div>
            <p className="text-gray-300 mb-4">
              Reset code generated for <strong className="text-white">{resetCodeResult.username}</strong>.
              Share this code securely with the user.
            </p>
            <div className="bg-navy-950 border border-gray-700 rounded-lg p-4 mb-3">
              <div className="flex items-center justify-between">
                <code className="text-2xl font-mono font-bold text-neon-cyan tracking-[0.3em]">
                  {resetCodeResult.code}
                </code>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(resetCodeResult.code);
                    setCodeCopied(true);
                    setTimeout(() => setCodeCopied(false), 2000);
                  }}
                  className="ml-3 p-2 rounded-lg hover:bg-gray-700/50 transition-colors"
                  title="Copy to clipboard"
                >
                  {codeCopied ? (
                    <Check className="h-5 w-5 text-green-400" />
                  ) : (
                    <Copy className="h-5 w-5 text-gray-400" />
                  )}
                </button>
              </div>
            </div>
            <p className="text-xs text-gray-500 mb-6">
              Expires: {new Date(resetCodeResult.expires_at).toLocaleString()}. Single use only.
            </p>
            <button
              onClick={() => setResetCodeResult(null)}
              className="w-full btn-primary"
            >
              Done
            </button>
          </div>
        </div>
      )}
      </div>
    </Layout>
  );
}
