import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Tv, Video, Settings, Music, PanelLeftClose, PanelLeftOpen, Box } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';

const Sidebar: React.FC = React.memo(() => {
  const { connected } = useTvAutomator();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div className="sidebar-brand">
        <div className="brand-icon-wrapper">
          <Box className="brand-icon" size={18} />
        </div>
        {!collapsed && (
          <div className="brand-text-block">
            <span className="brand-title">PressBox</span>
            <span className="brand-subtitle">Media Server</span>
          </div>
        )}
      </div>

      <nav className="nav-menu">
        <NavLink
          to="/mlb"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          title="MLB"
        >
          <Tv className="nav-icon" size={18} />
          {!collapsed && <span>MLB</span>}
        </NavLink>

        <NavLink
          to="/youtube"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          title="YouTube"
        >
          <Video className="nav-icon" size={18} />
          {!collapsed && <span>YouTube</span>}
        </NavLink>

        <NavLink
          to="/music"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          title="Music"
        >
          <Music className="nav-icon" size={18} />
          {!collapsed && <span>Music</span>}
        </NavLink>

        <NavLink
          to="/settings"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          title="Settings"
        >
          <Settings className="nav-icon" size={18} />
          {!collapsed && <span>Settings</span>}
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        {!collapsed && (
          <div className="status-indicator">
            <div className={`status-dot ${connected ? 'connected' : 'error'}`} />
            {connected ? 'Connected' : 'Connecting…'}
          </div>
        )}
        {collapsed && (
          <div className="status-indicator">
            <div className={`status-dot ${connected ? 'connected' : 'error'}`} />
          </div>
        )}
      </div>

      <button
        className="sidebar-toggle"
        onClick={() => setCollapsed(!collapsed)}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
      </button>
    </aside>
  );
});

export default Sidebar;
