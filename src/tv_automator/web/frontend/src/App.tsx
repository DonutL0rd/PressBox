import React from 'react';
import { Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import Dashboard from './views/Dashboard';
import YouTube from './views/YouTube';
import Settings from './views/Settings';
import Music from './views/Music';
import './layout.css';

const App: React.FC = () => {
  return (
    <div className="app-container">
      <Sidebar />
      <div className="main-wrap">
        <TopBar />
        <main className="content-area">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/youtube" element={<YouTube />} />
            <Route path="/music" element={<Music />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
};

export default App;
