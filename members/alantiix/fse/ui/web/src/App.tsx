import { Navigate, Route, Routes } from 'react-router-dom';
import { Shell } from './components/Shell';
import { ApiLogsPage } from './pages/ApiLogs';
import { CardsPage, LivePage } from './pages/Live';
import { JobPage, JobsPage } from './pages/Jobs';
import { CommandPage, CommandsPage } from './pages/Commands';
import { WorkflowsPage } from './pages/Workflows';
import { ComparePage, ExperimentsPage, RunPage, RunsPage } from './pages/Runs';
import { ConfigsPage } from './pages/Configs';
import { PluginsPage } from './pages/Plugins';
import { AnalyticsPage } from './pages/Analytics';
import { AuditPage, HealthPage, SecretsPage, SystemPage } from './pages/Settings';

export function AppRoutes() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/live" replace />} />
        <Route path="/live" element={<LivePage />} />
        <Route path="/cards" element={<CardsPage />} />
        <Route path="/api-logs" element={<ApiLogsPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:id" element={<JobPage />} />
        <Route path="/workflows" element={<WorkflowsPage />} />
        <Route path="/workflows/:tab" element={<WorkflowsPage />} />
        <Route path="/commands" element={<CommandsPage />} />
        <Route path="/commands/:id" element={<CommandPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/compare" element={<ComparePage />} />
        <Route path="/runs/:run" element={<RunPage />} />
        <Route path="/experiments" element={<ExperimentsPage />} />
        <Route path="/configs" element={<ConfigsPage />} />
        <Route path="/configs/:kind/:name" element={<ConfigsPage />} />
        <Route path="/plugins" element={<PluginsPage />} />
        <Route path="/secrets" element={<SecretsPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="*" element={<div className="empty">No such page.</div>} />
      </Routes>
    </Shell>
  );
}
