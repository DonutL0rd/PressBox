import React, { useEffect, useRef } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import NowPlayingBar from './components/NowPlayingBar';
import Dashboard from './views/Dashboard';
import YouTube from './views/YouTube';
import Settings from './views/Settings';
import Music from './views/Music';
import { useTvAutomator } from './hooks/useTvAutomator';
import './layout.css';

const AlertBanner: React.FC = () => {
  const { alert, clearAlert } = useTvAutomator();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!alert) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(clearAlert, 8000);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [alert?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!alert) return null;

  const bg = alert.level === 'error' ? 'var(--red)' : 'var(--green)';
  return (
    <div style={{
      position: 'fixed', top: '12px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 9999, background: bg, color: '#fff', borderRadius: '8px',
      padding: '10px 18px', display: 'flex', alignItems: 'center', gap: '12px',
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)', maxWidth: '480px', fontSize: '0.875rem',
    }}>
      <span style={{ flex: 1 }}>{alert.message}</span>
      <button
        onClick={clearAlert}
        style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', padding: '0 2px', fontSize: '1rem', lineHeight: 1 }}
        aria-label="Dismiss"
      >✕</button>
    </div>
  );
};

const App: React.FC = () => {
  return (
    <div className="app-container">
      <AlertBanner />
      <Sidebar />
      <div className="main-wrap">
        <main className="content-area">
          <Routes>
            <Route path="/" element={<Navigate to="/mlb" replace />} />
            <Route path="/mlb" element={<Dashboard />} />
            <Route path="/youtube" element={<YouTube />} />
            <Route path="/music" element={<Music />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
        <NowPlayingBar />
      </div>
    </div>
  );
};

export default App;
