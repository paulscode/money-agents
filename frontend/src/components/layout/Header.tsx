import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Zap, LogOut, User, Shield, Wrench, Target, Settings, ChevronDown, LayoutDashboard, FileText, Megaphone, CloudLightning, Bot, ListTodo, Bitcoin } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { BrainstormPanel } from '@/components/brainstorm/BrainstormPanel';
import NotificationBell from '@/components/NotificationBell';
import { walletService } from '@/services/wallet';
import { tasksService } from '@/services/tasks';

export function Header() {
  const { user, clearAuth } = useAuthStore();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isBrainstormOpen, setIsBrainstormOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const { data: walletConfig } = useQuery({
    queryKey: ['wallet-config'],
    queryFn: () => walletService.getConfig(),
    staleTime: 60000,
    retry: 1,
  });

  // Check for actionable tasks (for nav badge)
  const { data: taskCounts } = useQuery({
    queryKey: ['task-counts'],
    queryFn: () => tasksService.getCounts(),
    refetchInterval: 30000,
    staleTime: 25000,
    retry: 1,
  });

  const actionableTaskCount = taskCounts
    ? taskCounts.created + taskCounts.ready + taskCounts.blocked
    : 0;

  // Check if velocity breaker is tripped (for nav badge)
  const { data: breakerStatus } = useQuery({
    queryKey: ['velocity-breaker-status'],
    queryFn: () => walletService.getVelocityBreakerStatus(),
    enabled: !!walletConfig?.enabled,
    refetchInterval: 30000,
    staleTime: 25000,
    retry: 1,
  });

  const handleLogout = async () => {
    // RT-29: Revoke token server-side before clearing local state
    try {
      const token = sessionStorage.getItem('money_agents_token');
      if (token) {
        await fetch(`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/api/v1/auth/logout`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
        });
      }
    } catch {
      // Best-effort — proceed with client-side logout even if server call fails
    }
    clearAuth();
    window.location.href = '/login';
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <header className="bg-navy-900 border-b border-navy-700 shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* Logo */}
          <Link to="/" className="flex items-center space-x-2 group">
            <div className="relative">
              <Zap className="h-8 w-8 text-neon-cyan group-hover:text-neon-blue transition-colors" />
              <div className="absolute inset-0 blur-md bg-neon-cyan/30 group-hover:bg-neon-cyan/50 transition-all animate-pulse-slow"></div>
            </div>
            <span className="text-xl font-bold text-neon-cyan group-hover:text-neon-blue transition-colors">
              Money Agents
            </span>
          </Link>

          {/* Navigation */}
          <nav className="hidden md:flex space-x-6">
            <Link 
              to="/dashboard" 
              className="text-gray-300 hover:text-neon-cyan transition-colors flex items-center space-x-1"
            >
              <LayoutDashboard className="h-4 w-4" />
              <span>Dashboard</span>
            </Link>
            <Link 
              to="/opportunities" 
              className="text-gray-300 hover:text-neon-green transition-colors flex items-center space-x-1"
            >
              <Target className="h-4 w-4" />
              <span>Scout</span>
            </Link>
            <Link 
              to="/tasks" 
              className="text-gray-300 hover:text-neon-yellow transition-colors flex items-center space-x-1 relative"
            >
              <ListTodo className="h-4 w-4" />
              <span>Tasks</span>
              {actionableTaskCount > 0 && (
                <span className="absolute -top-1 -right-3 flex h-4 w-4">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-yellow opacity-75" />
                  <span className="relative inline-flex items-center justify-center rounded-full h-4 w-4 bg-neon-yellow text-navy-900 text-[10px] font-bold">
                    {actionableTaskCount > 9 ? '9+' : actionableTaskCount}
                  </span>
                </span>
              )}
            </Link>
            <Link 
              to="/proposals" 
              className="text-gray-300 hover:text-neon-cyan transition-colors flex items-center space-x-1"
            >
              <FileText className="h-4 w-4" />
              <span>Proposals</span>
            </Link>
            <Link 
              to="/campaigns" 
              className="text-gray-300 hover:text-neon-cyan transition-colors flex items-center space-x-1"
            >
              <Megaphone className="h-4 w-4" />
              <span>Campaigns</span>
            </Link>
            <Link 
              to="/tools" 
              className="text-gray-300 hover:text-neon-cyan transition-colors flex items-center space-x-1"
            >
              <Wrench className="h-4 w-4" />
              <span>Tools</span>
            </Link>
            {walletConfig?.enabled && (
              <Link 
                to="/budget" 
                className="text-gray-300 hover:text-neon-yellow transition-colors flex items-center space-x-1 relative"
              >
                <Bitcoin className="h-4 w-4" />
                <span>Budget</span>
                {breakerStatus?.is_tripped && (
                  <span className="absolute -top-1 -right-2 flex h-3 w-3">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                  </span>
                )}
              </Link>
            )}
            {user?.role === 'admin' && (
              <Link 
                to="/agents" 
                className="text-gray-300 hover:text-neon-purple transition-colors flex items-center space-x-1"
              >
                <Bot className="h-4 w-4" />
                <span>Agents</span>
              </Link>
            )}
          </nav>

          {/* Right side: Brainstorm + User Menu */}
          <div className="flex items-center space-x-3">
            {/* Brainstorm Button */}
            {user && (
              <button
                onClick={() => setIsBrainstormOpen(true)}
                className="relative p-2 text-gray-400 hover:text-neon-yellow transition-colors rounded-lg hover:bg-navy-800 group"
                title="Brainstorm - Quick AI Assistant"
              >
                <CloudLightning className="h-6 w-6" />
                <div className="absolute inset-0 blur-md bg-neon-yellow/0 group-hover:bg-neon-yellow/20 transition-all rounded-lg" />
              </button>
            )}

            {/* Notifications */}
            {user && <NotificationBell />}

            {/* Divider */}
            {user && (
              <div className="h-8 w-px bg-navy-600" />
            )}

            {/* User Menu */}
            {user && (
              <div className="relative" ref={dropdownRef}>
                <button
                  onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                  className="flex items-center space-x-2 text-gray-300 hover:text-neon-cyan transition-colors px-3 py-2 rounded-lg hover:bg-navy-800"
                >
                  {user.avatar_url && /^https?:\/\//i.test(user.avatar_url) ? (
                    <img 
                      src={user.avatar_url} 
                      alt={user.display_name || user.username}
                      className="h-8 w-8 rounded-full object-cover border-2 border-neon-cyan/50"
                    />
                  ) : (
                    <div className="h-8 w-8 rounded-full bg-navy-700 border-2 border-neon-cyan/50 flex items-center justify-center">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                  <span className="hidden sm:inline">{user.display_name || user.username}</span>
                  <ChevronDown className={`h-4 w-4 transition-transform ${isDropdownOpen ? 'rotate-180' : ''}`} />
                </button>

                {/* Dropdown Menu */}
                {isDropdownOpen && (
                  <div className="absolute right-0 mt-2 w-56 bg-navy-800 border border-navy-600 rounded-lg shadow-xl py-1 z-50">
                    {/* User Info */}
                    <div className="px-4 py-3 border-b border-navy-600">
                      <p className="text-sm font-medium text-white">{user.display_name || user.username}</p>
                      <p className="text-xs text-gray-400">{user.email}</p>
                    </div>

                    {/* Menu Items */}
                    <div className="py-1">
                      <Link
                        to="/settings/profile"
                        onClick={() => setIsDropdownOpen(false)}
                        className="flex items-center space-x-3 px-4 py-2 text-gray-300 hover:bg-navy-700 hover:text-neon-cyan transition-colors"
                      >
                        <Settings className="h-4 w-4" />
                        <span>Profile Settings</span>
                      </Link>
                    </div>

                    {/* Admin Section */}
                    {user.role === 'admin' && (
                      <div className="py-1 border-t border-navy-600">
                        <div className="px-4 py-1">
                          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Admin</p>
                        </div>
                        <Link
                          to="/admin/users"
                          onClick={() => setIsDropdownOpen(false)}
                          className="flex items-center space-x-3 px-4 py-2 text-gray-300 hover:bg-navy-700 hover:text-neon-purple transition-colors"
                        >
                          <Shield className="h-4 w-4" />
                          <span>Manage Users</span>
                        </Link>
                      </div>
                    )}

                    {/* Logout */}
                    <div className="py-1 border-t border-navy-600">
                      <button
                        onClick={handleLogout}
                        className="flex items-center space-x-3 w-full px-4 py-2 text-gray-300 hover:bg-navy-700 hover:text-neon-pink transition-colors"
                      >
                        <LogOut className="h-4 w-4" />
                        <span>Logout</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Brainstorm Panel */}
      <BrainstormPanel 
        isOpen={isBrainstormOpen} 
        onClose={() => setIsBrainstormOpen(false)} 
      />
    </header>
  );
}
