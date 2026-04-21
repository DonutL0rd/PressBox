import React, { useEffect, useState, useRef } from 'react';
import { Play, Pause, SkipForward, SkipBack, Shuffle, Repeat, Repeat1, Disc, Radio as RadioIcon, User, Mic, ListMusic, Plus, Trash2, Heart, Power } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import './Music.css';

type Tab = 'Albums' | 'Artists' | 'Radio';

const formatTime = (sec: number) => {
  if (!sec) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
};

// ── Inline music transport (shown when music is playing) ────────

const MusicTransport: React.FC = () => {
  const { music } = useTvAutomator();
  const song = music.song;
  const isPaused = music.paused ?? true;
  const duration = music.duration ?? 0;
  const RepeatIcon = music.repeat === 'one' ? Repeat1 : Repeat;

  const [pos, setPos] = useState(music.position ?? 0);
  const [seeking, setSeeking] = useState(false);
  const [liked, setLiked] = useState(false);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Sync position from WS
  useEffect(() => { if (!seeking) setPos(music.position ?? 0); }, [music.position, seeking]);

  // Local tick for smooth progress
  useEffect(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (music.playing && !isPaused && !seeking) {
      tickRef.current = setInterval(() => setPos(p => Math.min(p + 1, duration)), 1000);
    }
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [music.playing, isPaused, duration, seeking]);

  // Check liked status
  useEffect(() => {
    if (!song?.id) return;
    fetch('/api/music/starred').then(r => r.ok ? r.json() : null)
      .then((d: any) => { if (d) setLiked((d.song || []).some((s: any) => s.id === song.id)); })
      .catch(() => {});
  }, [song?.id]);

  const cmd = (command: string, value?: any) => fetch('/api/music/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value !== undefined ? { command, value } : { command }),
  }).catch(() => {});

  if (!song) return null;
  const pct = duration > 0 ? (pos / duration) * 100 : 0;

  return (
    <div className="mt-bar">
      <img src={`/api/music/cover/${song.albumId || song.id}?size=80`} className="mt-art" alt=""
        onError={(e: any) => { e.target.style.display = 'none'; }} />
      <div className="mt-info">
        <div className="mt-title">{song.title}</div>
        <div className="mt-artist">{song.artist}</div>
      </div>
      <div className="mt-transport">
        <button className={`btn-icon ${music.shuffle ? 'active' : ''}`} onClick={() => cmd('shuffle')}><Shuffle size={16} /></button>
        <button className="btn-icon" onClick={() => { setPos(0); cmd('prev'); }}><SkipBack size={20} fill="currentColor" /></button>
        <button className="btn-icon mt-play" onClick={() => cmd('toggle')}>
          {isPaused ? <Play size={24} fill="currentColor" /> : <Pause size={24} fill="currentColor" />}
        </button>
        <button className="btn-icon" onClick={() => { setPos(0); cmd('next'); }}><SkipForward size={20} fill="currentColor" /></button>
        <button className={`btn-icon ${music.repeat !== 'off' ? 'active' : ''}`} onClick={() => cmd('repeat')}><RepeatIcon size={16} /></button>
      </div>
      <div className="mt-progress">
        <span className="mt-time">{formatTime(pos)}</span>
        <input type="range" className="mt-seek" min={0} max={duration || 1} step={1} value={pos}
          style={{ '--pct': `${pct}%` } as React.CSSProperties}
          onMouseDown={() => setSeeking(true)} onTouchStart={() => setSeeking(true)}
          onChange={e => setPos(parseFloat(e.target.value))}
          onMouseUp={() => { setSeeking(false); cmd('seek', pos); }}
          onTouchEnd={() => { setSeeking(false); cmd('seek', pos); }}
        />
        <span className="mt-time">-{formatTime(Math.max(0, duration - pos))}</span>
      </div>
      <div className="mt-actions">
        <button className={`btn-icon ${liked ? 'active' : ''}`} onClick={() => {
          const v = !liked; setLiked(v);
          fetch('/api/music/star', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: song.id, action: v ? 'star' : 'unstar' }) });
        }} title={liked ? 'Unlike' : 'Like'}><Heart size={16} fill={liked ? 'var(--green)' : 'none'} color={liked ? 'var(--green)' : undefined} /></button>
        <button className="btn-icon" onClick={() => cmd('stop')} title="Stop"><Power size={16} /></button>
      </div>
    </div>
  );
};

const Music: React.FC = () => {
  const { queue: queueState, music } = useTvAutomator();
  const isMusicActive = !!(music.song && music.playing);

  const [activeTab, setActiveTab] = useState<Tab>('Albums');
  const [items, setItems] = useState<any[]>([]);

  const [selectedArtist, setSelectedArtist] = useState<any>(null);
  const [selectedAlbum, setSelectedAlbum] = useState<any>(null);

  const [showQueue, setShowQueue] = useState(false);

  const queue = queueState.songs;
  const queueIdx = queueState.index;

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
      extract = (d: any) => d.internetRadioStation || d.station || (Array.isArray(d) ? d : []);
    }

    if (endpoint) {
      fetch(endpoint)
        .then(r => r.json())
        .then(data => { const result = extract(data); setItems(Array.isArray(result) ? result : []); })
        .catch(console.error);
    }
  }, [activeTab]);

  useEffect(() => {
    if (selectedArtist && activeTab === 'Artists') {
      fetch(`/api/music/artist/${selectedArtist.id}`)
        .then(r => r.json())
        .then(data => { const albums = data.album || []; setItems(Array.isArray(albums) ? albums : []); });
    }
  }, [selectedArtist]);

  const handleCardClick = async (item: any) => {
    if (activeTab === 'Artists' && !selectedArtist) {
      setSelectedArtist(item);
      return;
    }
    if (activeTab === 'Radio') {
      playTracks([{ id: item.id, title: item.name, artist: 'Internet Radio' }]);
      return;
    }
    try {
      const r = await fetch(`/api/music/album/${item.id}`);
      const data = await r.json();
      setSelectedAlbum(data);
    } catch (e) {
      console.error(e);
    }
  };

  const playTracks = async (songs: any[], index: number = 0) => {
    await fetch('/api/music/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ songs, index })
    });
  };

  const appendToQueue = async (songs: any[]) => {
    await fetch('/api/music/queue/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ songs })
    });
  };

  const removeQueueItem = async (index: number) => {
    await fetch('/api/music/queue/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index })
    });
  };

  return (
    <div className="view-container animate-in music-container">
      <div className="music-content">
        <div className="page-header" style={{ marginBottom: '16px' }}>
          <div>
            <h1 className="page-title">Music</h1>
            <p className="page-subtitle">Subsonic / Navidrome Integrations</p>
          </div>
          <button className={`btn-icon ${showQueue ? 'active' : ''}`} onClick={() => setShowQueue(!showQueue)} title="Toggle Queue">
            <ListMusic size={20} />
          </button>
        </div>

        {/* Transport — visible when music is playing */}
        {isMusicActive && <MusicTransport />}

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

            <div className="music-grid" style={{flex: 1, padding: '16px', marginTop: '12px', background: 'var(--color-surface)', border: '1px solid var(--border-subtle)', borderRadius: '8px'}}>
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
          <div style={{flex: 1, padding: '20px', display: 'flex', flexDirection: 'column', background: 'var(--color-surface)', border: '1px solid var(--border-subtle)', borderRadius: '8px'}}>
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
