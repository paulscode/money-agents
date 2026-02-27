import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { User, Save, Camera, AlertCircle, Check } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { authService } from '@/services/auth';
import { Layout } from '@/components/layout/Layout';
import type { UserUpdate } from '@/types';

export function ProfileSettingsPage() {
  const { user, updateUser } = useAuthStore();
  const queryClient = useQueryClient();
  
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url || '');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const updateProfileMutation = useMutation({
    mutationFn: async (data: UserUpdate) => {
      return authService.updateCurrentUser(data);
    },
    onSuccess: (updatedUser) => {
      updateUser(updatedUser);
      setSuccess(true);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['user'] });
      setTimeout(() => setSuccess(false), 3000);
    },
    onError: (err: any) => {
      setError(err.response?.data?.detail || 'Failed to update profile');
      setSuccess(false);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    
    updateProfileMutation.mutate({
      display_name: displayName || undefined,
      avatar_url: avatarUrl || undefined,
    });
  };

  if (!user) {
    return null;
  }

  return (
    <Layout>
      <div className="max-w-2xl mx-auto px-4 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white mb-2">Profile Settings</h1>
        <p className="text-gray-400">Customize how you appear to others</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* Avatar Section */}
        <div className="bg-navy-800 rounded-xl border border-navy-600 p-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center space-x-2">
            <Camera className="h-5 w-5 text-neon-cyan" />
            <span>Profile Picture</span>
          </h2>
          
          <div className="flex items-start space-x-6">
            {/* Avatar Preview */}
            <div className="flex-shrink-0">
              {avatarUrl && /^https?:\/\//i.test(avatarUrl) ? (
                <img
                  src={avatarUrl}
                  alt="Avatar preview"
                  className="h-24 w-24 rounded-full object-cover border-4 border-neon-cyan/50"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              ) : (
                <div className="h-24 w-24 rounded-full bg-navy-700 border-4 border-neon-cyan/50 flex items-center justify-center">
                  <User className="h-12 w-12 text-gray-400" />
                </div>
              )}
            </div>
            
            {/* Avatar URL Input */}
            <div className="flex-1">
              <label htmlFor="avatarUrl" className="block text-sm font-medium text-gray-300 mb-2">
                Avatar URL
              </label>
              <input
                type="url"
                id="avatarUrl"
                value={avatarUrl}
                onChange={(e) => setAvatarUrl(e.target.value)}
                placeholder="https://example.com/avatar.jpg"
                className="w-full px-4 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-neon-cyan/50 focus:border-neon-cyan"
              />
              <p className="text-xs text-gray-500 mt-2">
                Enter a URL to an image (JPG, PNG, GIF). Recommended size: 256x256 pixels.
              </p>
            </div>
          </div>
        </div>

        {/* Display Name Section */}
        <div className="bg-navy-800 rounded-xl border border-navy-600 p-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center space-x-2">
            <User className="h-5 w-5 text-neon-cyan" />
            <span>Display Name</span>
          </h2>
          
          <div>
            <label htmlFor="displayName" className="block text-sm font-medium text-gray-300 mb-2">
              Display Name
            </label>
            <input
              type="text"
              id="displayName"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={user.username}
              maxLength={100}
              className="w-full px-4 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-neon-cyan/50 focus:border-neon-cyan"
            />
            <p className="text-xs text-gray-500 mt-2">
              This name will be shown instead of your username. Leave blank to use your username ({user.username}).
            </p>
          </div>
        </div>

        {/* Account Info (read-only) */}
        <div className="bg-navy-800 rounded-xl border border-navy-600 p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Account Information</h2>
          
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Username</label>
              <p className="text-white">{user.username}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Email</label>
              <p className="text-white">{user.email}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Role</label>
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                user.role === 'admin' 
                  ? 'bg-neon-purple/20 text-neon-purple' 
                  : 'bg-neon-cyan/20 text-neon-cyan'
              }`}>
                {user.role}
              </span>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Member Since</label>
              <p className="text-white">{new Date(user.created_at).toLocaleDateString()}</p>
            </div>
          </div>
        </div>

        {/* Error/Success Messages */}
        {error && (
          <div className="flex items-center space-x-2 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400">
            <AlertCircle className="h-5 w-5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div className="flex items-center space-x-2 p-4 bg-neon-green/10 border border-neon-green/30 rounded-lg text-neon-green">
            <Check className="h-5 w-5 flex-shrink-0" />
            <span>Profile updated successfully!</span>
          </div>
        )}

        {/* Submit Button */}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={updateProfileMutation.isPending}
            className="flex items-center space-x-2 px-6 py-3 bg-neon-cyan text-navy-900 font-semibold rounded-lg hover:bg-neon-cyan/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {updateProfileMutation.isPending ? (
              <>
                <div className="animate-spin h-5 w-5 border-2 border-navy-900 border-t-transparent rounded-full" />
                <span>Saving...</span>
              </>
            ) : (
              <>
                <Save className="h-5 w-5" />
                <span>Save Changes</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
    </Layout>
  );
}
