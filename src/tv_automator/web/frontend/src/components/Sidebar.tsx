import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Tv, Video, Settings, Play, Music, Radio, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';

const Sidebar: React.FC = () => {
  const { connected } = useTvAutomator();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div className="sidebar-brand">
        <div className="brand-icon-wrapper">
          <Play className="brand-icon" size={20} fill="currentColor" />
        </div>
        {!collapsed && (
          <div className="brand-text-block">
            <span className="brand-title tracking-tight">TV Automator</span>
            <span className="brand-subtitle">Controller View</span>
          </div>
        )}
      </div>

      <nav className="nav-menu">
        <NavLink 
          to="/" 
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          title="Now Playing"
        >
          <Radio className="nav-icon" size={18} />
          {!collapsed && <span>Now Playing</span>}
        </NavLink>

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
            {connected ? 'WS Connected' : 'Connecting...'}
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
        {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
      </button>
    </aside>
  );
};

export default Sidebar;
