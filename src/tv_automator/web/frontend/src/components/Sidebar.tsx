import React from 'react';
import { NavLink } from 'react-router-dom';
import { Tv, Video, Settings, Play, Music, Radio } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';

const Sidebar: React.FC = () => {
  const { connected } = useTvAutomator();

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-icon-wrapper">
          <Play className="brand-icon" size={20} fill="currentColor" />
        </div>
        <div className="brand-text-block">
          <span className="brand-title tracking-tight">TV Automator</span>
          <span className="brand-subtitle">Controller View</span>
        </div>
      </div>

      <nav className="nav-menu">
        <NavLink 
          to="/" 
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Radio className="nav-icon" size={18} />
          <span>Now Playing</span>
        </NavLink>

        <NavLink 
          to="/mlb" 
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Tv className="nav-icon" size={18} />
          <span>MLB</span>
        </NavLink>
        
        <NavLink 
          to="/youtube" 
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Video className="nav-icon" size={18} />
          <span>YouTube</span>
        </NavLink>

        <NavLink
          to="/music"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Music className="nav-icon" size={18} />
          <span>Music</span>
        </NavLink>

        <NavLink
          to="/settings" 
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Settings className="nav-icon" size={18} />
          <span>Settings</span>
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        <div className="status-indicator">
          <div className={`status-dot ${connected ? 'connected' : 'error'}`} />
          {connected ? 'WS Connected' : 'Connecting...'}
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
