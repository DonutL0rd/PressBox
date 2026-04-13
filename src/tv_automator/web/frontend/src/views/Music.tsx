import React, { useEffect, useState } from 'react';
import { Play, Pause, SkipForward, SkipBack, Disc, Radio as RadioIcon, User, Mic } from 'lucide-react';
import './Music.css';

type Tab = 'Albums' | 'Artists' | 'Radio';

const Music: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>('Albums');
  const [items, setItems] = useState<any[]>([]);
  const [selectedArtist, setSelectedArtist] = useState<any>(null); // When an artist is clicked, holds artist object
  const [nowPlaying, setNowPlaying] = useState<any>(null);
  const [isPaused, setIsPaused] = useState(false);

  // Status poller
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const r = await fetch('/api/music/status');
        const data = await r.json();
        setNowPlaying(data.song);
        setIsPaused(data.paused);
      } catch (e) {}
    };
    fetchStatus();
    const iv = setInterval(fetchStatus, 2000);
    return () => clearInterval(iv);
  }, []);

  // Fetch library data based on tab
  useEffect(() => {
    setItems([]);
    setSelectedArtist(null);
    let endpoint = '';
    let extract = (d: any) => d;
    
    if (activeTab === 'Albums') {
      endpoint = '/api/music/albums';
      extract = (d: any) => d.album || [];
    } else if (activeTab === 'Artists') {
      endpoint = '/api/music/artists';
      extract = (d: any) => (d.index || []).flatMap((idx: any) => idx.artist || []);
    } else if (activeTab === 'Radio') {
      endpoint = '/api/music/radio';
      extract = (d: any) => d || [];
    }

    if (endpoint) {
      fetch(endpoint)
        .then(r => r.json())
        .then(data => setItems(extract(data)))
        .catch(console.error);
    }
  }, [activeTab]);

  // When viewing an artist, fetch their albums
  useEffect(() => {
    if (selectedArtist && activeTab === 'Artists') {
      fetch(`/api/music/artist/${selectedArtist.id}`)
        .then(r => r.json())
        .then(data => {
          setItems(data.album || []);
        });
    }
  }, [selectedArtist]);

  const handleItemClick = async (item: any) => {
    // If clicking an artist in the Artists list, drill down
    if (activeTab === 'Artists' && !selectedArtist) {
      setSelectedArtist(item);
      return;
    }

    let songs = [];
    if (activeTab === 'Radio') {
      // It's a radio station
      songs = [{ id: item.id, title: item.name, artist: 'Internet Radio' }];
    } else {
      // It's an album
      try {
        const r = await fetch(`/api/music/album/${item.id}`);
        const data = await r.json();
        songs = data.song || [];
      } catch (e) {
        console.error("Failed to load album songs", e);
        return;
      }
    }

    if (songs.length > 0) {
      fetch('/api/music/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ songs, index: 0 })
      }).then(fetchStatusNow);
    }
  };

  const fetchStatusNow = async () => {
    const r = await fetch('/api/music/status');
    const data = await r.json();
    setNowPlaying(data.song);
    setIsPaused(data.paused);
  };

  const sendCommand = async (cmd: string) => {
    await fetch('/api/music/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    });
    fetchStatusNow();
  };

  return (
    <div className="view-container animate-in music-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">Music</h1>
          <p className="page-subtitle">Subsonic / Navidrome Integrations</p>
        </div>
      </div>

      <div className="music-tabs">
        <button className={`music-tab ${activeTab === 'Albums' ? 'active' : ''}`} onClick={() => setActiveTab('Albums')}>
          <Disc size={16} style={{display:'inline', marginRight: 6, verticalAlign:'text-bottom'}}/> 
          Recent Albums
        </button>
        <button className={`music-tab ${activeTab === 'Artists' ? 'active' : ''}`} onClick={() => setActiveTab('Artists')}>
          <User size={16} style={{display:'inline', marginRight: 6, verticalAlign:'text-bottom'}}/> 
          Artists
        </button>
        <button className={`music-tab ${activeTab === 'Radio' ? 'active' : ''}`} onClick={() => setActiveTab('Radio')}>
          <RadioIcon size={16} style={{display:'inline', marginRight: 6, verticalAlign:'text-bottom'}}/> 
          Internet Radio
        </button>
      </div>

      <div className="music-grid glass-panel" style={{flex: 1, padding: '24px'}}>
        {activeTab === 'Artists' && selectedArtist && (
          <div style={{gridColumn: '1 / -1', marginBottom: '16px'}}>
            <button className="btn" onClick={() => { setSelectedArtist(null); setActiveTab('Artists'); }}>
              &larr; Back to Artists
            </button>
            <h2 style={{marginTop: '16px'}}>{selectedArtist.name} Albums</h2>
          </div>
        )}
        
        {items.map((item: any, idx: number) => (
          <div key={`${item.id}-${idx}`} className="media-card" onClick={() => handleItemClick(item)}>
            {(activeTab === 'Radio' || (!item.coverArt && !item.id)) ? (
              <div className="media-icon-fallback">
                 {activeTab === 'Radio' ? <RadioIcon size={48} color="var(--text-tertiary)" /> : <Mic size={48} color="var(--text-tertiary)" />}
              </div>
            ) : (
              <img 
                src={`/api/music/cover/${activeTab === 'Artists' && !selectedArtist ? item.id : item.coverArt || item.id}`} 
                className="media-art" 
                alt={item.title || item.name} 
                onError={(e: any) => { e.target.style.display = 'none'; }}
              />
            )}
            <div className="media-info">
              <div className="media-title">{item.title || item.name}</div>
              <div className="media-subtitle">
                {activeTab === 'Artists' && !selectedArtist 
                  ? `${item.albumCount || 0} Albums`
                  : item.artist || item.homePageUrl || ''}
              </div>
            </div>
          </div>
        ))}
        {items.length === 0 && <p style={{color: 'var(--text-secondary)'}}>Loading library...</p>}
      </div>

      <div className="transport-bar">
        {nowPlaying ? (
          <div className="now-playing-info">
            <img src={`/api/music/cover/${nowPlaying.albumId || nowPlaying.id}`} className="now-playing-art" alt="Cover" />
            <div>
              <div style={{fontWeight: 600, color: 'var(--neon-green)'}}>{nowPlaying.title}</div>
              <div style={{fontSize: '0.85rem', color: 'var(--text-secondary)'}}>{nowPlaying.artist}</div>
            </div>
          </div>
        ) : (
          <div className="now-playing-info" style={{color: 'var(--text-secondary)'}}>
            Not Playing
          </div>
        )}
        
        <div className="transport-controls">
          <button className="btn-icon" onClick={() => sendCommand('prev')}><SkipBack size={24} /></button>
          
          <button className="btn-icon" style={{background: 'rgba(0, 255, 170, 0.1)', color: 'var(--neon-green)', padding: '12px'}} onClick={() => sendCommand('toggle')}>
            {isPaused || !nowPlaying ? <Play size={24} fill="currentColor" /> : <Pause size={24} fill="currentColor" />}
          </button>
          
          <button className="btn-icon" onClick={() => sendCommand('next')}><SkipForward size={24} /></button>
        </div>
      </div>
    </div>
  );
};

export default Music;
