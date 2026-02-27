import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginForm } from '@/components/auth/LoginForm';
import { RegisterForm } from '@/components/auth/RegisterForm';
import { ResetPasswordForm } from '@/components/auth/ResetPasswordForm';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { DashboardPage } from '@/pages/DashboardPage';
import { ProposalsPage } from '@/pages/ProposalsPage';
import { ProposalDetailPage } from '@/pages/ProposalDetailPage';
import { ProposalCreatePage } from '@/pages/ProposalCreatePage';
import { CampaignsPage } from '@/pages/CampaignsPage';
import { CampaignDetailPage } from '@/pages/CampaignDetailPage';
import { AdminUsersPage } from '@/pages/AdminUsersPage';
import { ToolsPage } from '@/pages/ToolsPage';
import { ToolCreatePage } from '@/pages/ToolCreatePage';
import { ToolEditPage } from '@/pages/ToolEditPage';
import { ToolDetailPage } from '@/pages/ToolDetailPage';
import { OpportunitiesPage } from '@/pages/OpportunitiesPage';
import { ProfileSettingsPage } from '@/pages/ProfileSettingsPage';
import { TasksPage } from '@/pages/TasksPage';
import AgentManagementPage from '@/pages/AgentManagementPage';
import { WalletPage } from '@/pages/WalletPage';
import { BudgetPage } from '@/pages/BudgetPage';
import { BackgroundTestPage } from '@/pages/BackgroundTestPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginForm />} />
          <Route path="/register" element={<RegisterForm />} />
          <Route path="/reset-password" element={<ResetPasswordForm />} />
          
          {/* Temporary background test page */}
          <Route path="/bg-test" element={<BackgroundTestPage />} />
          
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/proposals"
            element={
              <ProtectedRoute>
                <ProposalsPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/proposals/new"
            element={
              <ProtectedRoute>
                <ProposalCreatePage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/proposals/:id/edit"
            element={
              <ProtectedRoute>
                <ProposalCreatePage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/proposals/:id"
            element={
              <ProtectedRoute>
                <ProposalDetailPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/campaigns"
            element={
              <ProtectedRoute>
                <CampaignsPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/campaigns/:id"
            element={
              <ProtectedRoute>
                <CampaignDetailPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/opportunities"
            element={
              <ProtectedRoute>
                <OpportunitiesPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/tasks"
            element={
              <ProtectedRoute>
                <TasksPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/agents"
            element={
              <ProtectedRoute>
                <AgentManagementPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/wallet"
            element={
              <ProtectedRoute>
                <WalletPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/budget"
            element={
              <ProtectedRoute>
                <BudgetPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/admin/users"
            element={
              <ProtectedRoute>
                <AdminUsersPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/tools"
            element={
              <ProtectedRoute>
                <ToolsPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/tool-operations"
            element={
              <Navigate to="/tools?tab=operations" replace />
            }
          />
          
          <Route
            path="/tools/new"
            element={
              <ProtectedRoute>
                <ToolCreatePage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/tools/:id/edit"
            element={
              <ProtectedRoute>
                <ToolEditPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/tools/:id"
            element={
              <ProtectedRoute>
                <ToolDetailPage />
              </ProtectedRoute>
            }
          />
          
          <Route
            path="/resources"
            element={
              <Navigate to="/tools?tab=resources" replace />
            }
          />
          
          <Route
            path="/media-library"
            element={
              <Navigate to="/tools?tab=media" replace />
            }
          />
          
          <Route
            path="/admin/remote-agents"
            element={
              <Navigate to="/agents?tab=remote" replace />
            }
          />
          
          <Route
            path="/usage"
            element={
              <Navigate to="/dashboard" replace />
            }
          />
          
          <Route
            path="/settings/profile"
            element={
              <ProtectedRoute>
                <ProfileSettingsPage />
              </ProtectedRoute>
            }
          />
          
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
