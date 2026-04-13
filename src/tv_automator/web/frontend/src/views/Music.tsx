import React, { useEffect, useState } from 'react';
import { Play, Pause, SkipForward, SkipBack, Disc, Radio as RadioIcon, User, Mic, ListMusic, Volume2, VolumeX, Plus, Trash2 } from 'lucide-react';
import './Music.css';

type Tab = 'Albums' | 'Artists' | 'Radio';

const formatTime = (sec: number) => {
  if (!sec) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
};

const Music: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>('Albums');
  const [items, setItems] = useState<any[]>([]);
  
  // Drill-down states
  const [selectedArtist, setSelectedArtist] = useState<any>(null); // holds artist object
  const [selectedAlbum, setSelectedAlbum] = useState<any>(null);   // holds populated album object (with tracks)

  // Subsystem states
  const [nowPlaying, setNowPlaying] = useState<any>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [vol, setVol] = useState(50);
  const [isMuted, setIsMuted] = useState(false);
  
  // Queue state
  const [showQueue, setShowQueue] = useState(false);
  const [queue, setQueue] = useState<any[]>([]);
  const [queueIdx, setQueueIdx] = useState(-1);

  const fetchSubsystems = async () => {
    try {
      const [stRes, qRes, vRes] = await Promise.all([
        fetch('/api/music/status'),
        fetch('/api/music/queue'),
        fetch('/api/volume')
      ]);
      const st = await stRes.json();
      const q = await qRes.json();
      const v = await vRes.json();
      
      setNowPlaying(st.song);
      setIsPaused(st.paused);
      setQueue(q.queue || []);
      setQueueIdx(q.index);
      setVol(v.volume);
      setIsMuted(v.muted);
    } catch (e) {}
  };

  useEffect(() => {
    fetchSubsystems();
    const iv = setInterval(fetchSubsystems, 2000);
    return () => clearInterval(iv);
  }, []);

  // Fetch library data based on tab
  useEffect(() => {
    setItems([]);
    setSelectedArtist(null);
    setSelectedAlbum(null);
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
        .then(data => setItems(data.album || []));
    }
  }, [selectedArtist]);

  const handleCardClick = async (item: any) => {
    if (activeTab === 'Artists' && !selectedArtist) {
      setSelectedArtist(item);
      return;
    }
    if (activeTab === 'Radio') {
      // Direct play for radio
      playTracks([{ id: item.id, title: item.name, artist: 'Internet Radio' }]);
      return;
    }
    // It's an album — fetch tracklist
    try {
      const r = await fetch(`/api/music/album/${item.id}`);
      const data = await r.json();
      setSelectedAlbum(data);
    } catch (e) {
      console.error(e);
    }
  };

  // Commands
  const sendCommand = async (cmd: string) => {
    await fetch('/api/music/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    });
    fetchSubsystems();
  };

  const playTracks = async (songs: any[], index: number = 0) => {
    await fetch('/api/music/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ songs, index })
    });
    fetchSubsystems();
  };

  const appendToQueue = async (songs: any[]) => {
    await fetch('/api/music/queue/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ songs })
    });
    fetchSubsystems();
  };

  const removeQueueItem = async (index: number) => {
    await fetch('/api/music/queue/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index })
    });
    fetchSubsystems();
  };

  const handleVolumeChange = (e: any) => {
    const val = parseInt(e.target.value);
    setVol(val);
    fetch(`/api/volume?level=${val}`, { method: 'POST' });
  };

  return (
    <div className="view-container animate-in music-container">
      <div className="music-content">
        <div className="page-header" style={{ marginBottom: '16px' }}>
          <div>
            <h1 className="page-title">Music</h1>
            <p className="page-subtitle">Subsonic / Navidrome Integrations</p>
          </div>
        </div>

        {!selectedAlbum ? (
          <>
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

            <div className="music-grid glass-panel" style={{flex: 1, padding: '24px', marginTop: '16px'}}>
              {activeTab === 'Artists' && selectedArtist && (
                <div style={{gridColumn: '1 / -1', marginBottom: '16px'}}>
                  <button className="btn" onClick={() => { setSelectedArtist(null); }}>
                    &larr; Back to Artists
                  </button>
                  <h2 style={{marginTop: '16px'}}>{selectedArtist.name} Albums</h2>
                </div>
              )}
              
              {items.map((item: any, idx: number) => (
                <div key={`${item.id}-${idx}`} className="media-card" onClick={() => handleCardClick(item)}>
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
          </>
        ) : (
          /* Album Tracklist View */
          <div className="glass-panel" style={{flex: 1, padding: '24px', display: 'flex', flexDirection: 'column'}}>
            <button className="btn" style={{alignSelf: 'flex-start', marginBottom: '24px'}} onClick={() => setSelectedAlbum(null)}>
              &larr; Back to Library
            </button>
            <div style={{display: 'flex', gap: '24px', marginBottom: '24px'}}>
              <img 
                src={`/api/music/cover/${selectedAlbum.coverArt || selectedAlbum.id}?size=200`} 
                style={{width: '200px', height: '200px', borderRadius: '12px', boxShadow: '0 8px 24px rgba(0,0,0,0.3)'}}
                alt="" 
              />
              <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'flex-end'}}>
                <h1 style={{fontSize: '2rem', marginBottom: '8px'}}>{selectedAlbum.title || selectedAlbum.name}</h1>
                <h3 style={{color: 'var(--text-secondary)'}}>{selectedAlbum.artist} • {selectedAlbum.year || ''}</h3>
                <div style={{display: 'flex', gap: '12px', marginTop: '24px'}}>
                  <button className="btn btn-primary" onClick={() => playTracks(selectedAlbum.song || [])}>
                    <Play size={16} /> Play All
                  </button>
                  <button className="btn" onClick={() => appendToQueue(selectedAlbum.song || [])}>
                    <Plus size={16} /> Queue All
                  </button>
                </div>
              </div>
            </div>

            <div className="track-list" style={{flex: 1, overflowY: 'auto'}}>
              {(selectedAlbum.song || []).map((tr: any, idx: number) => (
                <div key={tr.id} className="track-item">
                  <div className="track-number">{tr.track || idx + 1}</div>
                  <div className="track-title">{tr.title}</div>
                  <div className="track-duration">{formatTime(tr.duration)}</div>
                  <button className="btn-icon" title="Play Now" onClick={(e) => { e.stopPropagation(); playTracks(selectedAlbum.song, idx); }}><Play size={16}/></button>
                  <button className="btn-icon" title="Add to Queue" onClick={(e) => { e.stopPropagation(); appendToQueue([tr]); }}><Plus size={16}/></button>
                </div>
              ))}
            </div>
          </div>
        )}

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

          <div className="volume-control">
            <button className="btn-icon" onClick={() => fetch(`/api/volume?mute=${!isMuted}`, {method:'POST'})}>
               {isMuted || vol === 0 ? <VolumeX size={18} color="var(--text-secondary)" /> : <Volume2 size={18} color="var(--text-secondary)" />}
            </button>
            <input 
              type="range" 
              className="volume-slider" 
              min="0" max="100" 
              value={vol} 
              onChange={handleVolumeChange} 
            />
          </div>
          
          <button className={`btn-icon ${showQueue ? 'active' : ''}`} style={{marginLeft: '16px'}} onClick={() => setShowQueue(!showQueue)}>
            <ListMusic size={20} />
          </button>
        </div>
      </div>

      {showQueue && (
        <div className="queue-panel animate-in">
          <div className="queue-header">
            <span>Play Queue</span>
            <span style={{fontSize: '0.8rem', color: 'var(--text-tertiary)'}}>{queue.length} Tracks</span>
          </div>
          <div className="queue-list">
             {queue.map((q, idx) => (
                <div key={`${q.id}-${idx}`} className={`queue-item ${idx === queueIdx ? 'active' : ''}`}>
                  <div className="queue-item-info">
                    <div className="queue-item-title">{q.title}</div>
                    <div className="queue-item-artist">{q.artist}</div>
                  </div>
                  <button className="btn-icon" style={{padding: '4px'}} onClick={() => removeQueueItem(idx)}>
                    <Trash2 size={14} color="var(--text-tertiary)"/>
                  </button>
                </div>
             ))}
             {queue.length === 0 && (
               <div style={{padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)'}}>
                 Queue is empty
               </div>
             )}
          </div>
        </div>
      )}
    </div>
  );
};

export default Music;
