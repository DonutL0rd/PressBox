import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, Power, Shuffle, Repeat, Repeat1, Tv, Monitor, Subtitles, Layers, Heart, Trash2, Music } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import './NowPlaying.css';

const fmt = (sec: number) => {
  if (!sec || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
};

const firePost = (url: string, body?: any) => {
  fetch(url, {
    method: 'POST',
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  }).catch(() => {});
};

// Animated equalizer bars for the active track
const EqBars: React.FC = () => (
  <div className="eq-bars">
    <span className="eq-bar" /><span className="eq-bar" /><span className="eq-bar" /><span className="eq-bar" />
  </div>
);

const NowPlaying: React.FC = () => {
  const { status, games, stopPlayback } = useTvAutomator();

  const [music, setMusic] = useState<any>(null);
  const [queue, setQueue] = useState<any[]>([]);
  const [queueIdx, setQueueIdx] = useState(-1);
  const [vol, setVol] = useState(50);
  const [isMuted, setIsMuted] = useState(false);
  const volTimeout = useRef<any>(null);
  const [isLiked, setIsLiked] = useState(false);

  // Global Controls State
  const [settings, setSettings] = useState<any>({});
  const [levels, setLevels] = useState<any[]>([]);
  const [cec, setCec] = useState<any>({});
  const [ccEnabled, setCcEnabled] = useState(false);

  // Local position for smooth progress bar
  const [localPos, setLocalPos] = useState(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [isSeeking, setIsSeeking] = useState(false);

  // ── Polling ────────────────────────────────────────────────

  const fetchFast = useCallback(async () => {
    try {
      const [stRes, vRes] = await Promise.all([
        fetch('/api/music/status'),
        fetch('/api/volume'),
      ]);
      if (stRes.ok) {
        const st = await stRes.json();
        setMusic(st);
        // Only overwrite position if user isn't actively seeking
        if (!isSeeking) setLocalPos(st.position ?? 0);
      }
      if (vRes.ok) {
        const v = await vRes.json();
        if (!volTimeout.current) {
          setVol(v.volume);
          setIsMuted(v.muted);
        }
      }
    } catch {}
  }, [isSeeking]);

  const fetchSlow = useCallback(async () => {
    try {
      const [setRes, lvlRes, cecRes, qRes] = await Promise.all([
        fetch('/api/settings'),
        fetch('/api/player/levels'),
        fetch('/api/cec/status'),
        fetch('/api/music/queue'),
      ]);
      if (setRes.ok) setSettings(await setRes.json());
      if (lvlRes.ok) setLevels((await lvlRes.json()).levels || []);
      if (cecRes.ok) setCec(await cecRes.json());
      if (qRes.ok) {
        const q = await qRes.json();
        setQueue(q.songs || []);
        setQueueIdx(q.index ?? -1);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchFast();
    fetchSlow();
    const fastIv = setInterval(fetchFast, 3000);
    const slowIv = setInterval(fetchSlow, 15000);
    return () => { clearInterval(fastIv); clearInterval(slowIv); };
  }, [fetchFast, fetchSlow]);

  // Smooth progress ticker
  useEffect(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (music?.playing && !music?.paused && !isSeeking) {
      tickRef.current = setInterval(() => {
        setLocalPos(p => Math.min(p + 1, music?.duration ?? p));
      }, 1000);
    }
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [music?.playing, music?.paused, music?.duration, isSeeking]);

  // ── Music Commands — optimistic + fire-and-forget ──────────

  const cmdToggle = () => {
    setMusic((prev: any) => prev ? { ...prev, paused: !prev.paused } : prev);
    firePost('/api/music/command', { command: 'toggle' });
  };

  const cmdNext = () => {
    setLocalPos(0);
    if (queue.length > 0) {
      const nextIdx = (queueIdx + 1) % queue.length;
      setQueueIdx(nextIdx);
      setMusic((prev: any) => prev ? { ...prev, song: queue[nextIdx], paused: false } : prev);
    }
    firePost('/api/music/command', { command: 'next' });
    setTimeout(fetchSlow, 500);
  };

  const cmdPrev = () => {
    setLocalPos(0);
    // If more than 3s in, just restart the song (server handles this)
    if (localPos > 3) {
      firePost('/api/music/command', { command: 'prev' });
      return;
    }
    if (queue.length > 0 && queueIdx > 0) {
      const prevIdx = queueIdx - 1;
      setQueueIdx(prevIdx);
      setMusic((prev: any) => prev ? { ...prev, song: queue[prevIdx], paused: false } : prev);
    }
    firePost('/api/music/command', { command: 'prev' });
    setTimeout(fetchSlow, 500);
  };

  const cmdShuffle = () => {
    setMusic((prev: any) => prev ? { ...prev, shuffle: !prev.shuffle } : prev);
    firePost('/api/music/command', { command: 'shuffle' });
  };

  const cmdRepeat = () => {
    const modes = ['off', 'all', 'one'];
    const cur = music?.repeat || 'off';
    const idx = modes.indexOf(cur);
    const next = modes[(idx + 1) % 3];
    setMusic((prev: any) => prev ? { ...prev, repeat: next } : prev);
    firePost('/api/music/command', { command: 'repeat' });
  };

  const cmdJump = (index: number) => {
    setQueueIdx(index);
    setLocalPos(0);
    setMusic((prev: any) => prev ? { ...prev, song: queue[index], paused: false } : prev);
    firePost('/api/music/command', { command: 'jump', value: index });
    setTimeout(fetchSlow, 500);
  };

  const cmdRemoveFromQueue = (index: number) => {
    // Optimistic remove
    setQueue(prev => prev.filter((_, i) => i !== index));
    if (index < queueIdx) setQueueIdx(prev => prev - 1);
    firePost('/api/music/queue/remove', { index });
  };

  const cmdStop = () => {
    setMusic(null);
    setQueue([]);
    setQueueIdx(-1);
    setLocalPos(0);
    firePost('/api/music/command', { command: 'stop' });
  };

  // ── Like/Star ──────────────────────────────────────────────

  const toggleLike = () => {
    const song = music?.song;
    if (!song) return;
    const newLiked = !isLiked;
    setIsLiked(newLiked);
    firePost('/api/music/star', { id: song.id, action: newLiked ? 'star' : 'unstar' });
  };

  // ── Seek — with drag tracking ─────────────────────────────

  const onSeekStart = () => setIsSeeking(true);
  const onSeekChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const pos = parseFloat(e.target.value);
    setLocalPos(pos);
  };
  const onSeekEnd = () => {
    setIsSeeking(false);
    firePost('/api/music/command', { command: 'seek', value: localPos });
  };

  // ── Volume ─────────────────────────────────────────────────

  const handleVol = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseInt(e.target.value);
    setVol(val);
    if (volTimeout.current) clearTimeout(volTimeout.current);
    volTimeout.current = setTimeout(() => {
      fetch(`/api/volume?level=${val}`, { method: 'POST' }).catch(() => {});
      volTimeout.current = null;
    }, 150);
  };

  const toggleMute = () => {
    const newMuted = !isMuted;
    setIsMuted(newMuted);
    fetch(`/api/volume?mute=${newMuted}`, { method: 'POST' }).catch(() => {});
  };

  // ── Settings + Player ──────────────────────────────────────

  const updateSetting = (payload: any) => {
    setSettings((prev: any) => ({ ...prev, ...payload }));
    firePost('/api/settings', payload);
  };

  const sendPlayerCommand = (cmd: any) => firePost('/api/player/command', cmd);
  const toggleCec = (action: 'on' | 'off') => firePost(`/api/cec/${action}`);

  // ── Derived state ──────────────────────────────────────────

  const isPlayingGame    = !!status.now_playing_game_id;
  const isPlayingYoutube = status.youtube_mode && !isPlayingGame;
  const song             = music?.song;
  const isMusicActive    = !!(song && !isPlayingGame && !isPlayingYoutube);
  const isIdle           = !isPlayingGame && !isPlayingYoutube && !isMusicActive;

  const game = isPlayingGame ? games.find(g => g.game_id === status.now_playing_game_id) : null;

  const duration = music?.duration ?? 0;
  const progress = duration > 0 ? (localPos / duration) * 100 : 0;
  const RepeatIcon = music?.repeat === 'one' ? Repeat1 : Repeat;
  const isPaused  = music?.paused ?? true;

  return (
    <div className="np-page animate-in">
      <div className="np-main-content">
        {/* ── Idle ──────────────────────────────────────────── */}
        {isIdle && (
          <div className="np-idle">
            <div className="np-idle-icon">
              <Music size={48} />
            </div>
            <h2 className="np-idle-title">Nothing Playing</h2>
            <p className="np-idle-sub">Start a game, YouTube video, or music to see it here.</p>
          </div>
        )}

        {/* ── Game ──────────────────────────────────────────── */}
        {isPlayingGame && (
          <div className="np-tv">
            <div className="np-tv-badge">
              <Tv size={16} />
              <span className="live-pulse-dot" />
              {game?.status_label ?? 'LIVE'}
            </div>
            {game ? (
              <div className="np-matchup">
                <div className="np-team">
                  <div className="np-team-abbr">{game.away_team.abbreviation}</div>
                  <div className="np-team-name">{game.away_team.name}</div>
                  <div className="np-team-score">{game.away_team.score ?? '—'}</div>
                </div>
                <div className="np-vs">
                  <span>vs</span>
                  <div className="np-game-meta">{game.venue}</div>
                </div>
                <div className="np-team">
                  <div className="np-team-abbr">{game.home_team.abbreviation}</div>
                  <div className="np-team-name">{game.home_team.name}</div>
                  <div className="np-team-score">{game.home_team.score ?? '—'}</div>
                </div>
              </div>
            ) : (
              <div className="np-tv-label">Game Playing on TV</div>
            )}
            <button className="btn btn-primary np-stop" onClick={stopPlayback}>
              <Power size={16} /> Stop Playback
            </button>
          </div>
        )}

        {/* ── YouTube ───────────────────────────────────────── */}
        {isPlayingYoutube && (
          <div className="np-tv np-yt">
            <div className="np-yt-icon">
              <svg width="64" height="64" viewBox="0 0 24 24" fill="currentColor">
                <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93-.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
              </svg>
            </div>
            <div className="np-tv-badge np-tv-badge--yt">
              <span className="live-pulse-dot" style={{ background: 'var(--neon-red)' }} />
              Playing on TV
            </div>
            <div className="np-tv-label">YouTube</div>
            <button className="btn btn-primary np-stop np-stop--yt" onClick={stopPlayback}>
              <Power size={16} /> Stop Playback
            </button>
          </div>
        )}

        {/* ── Music Player ──────────────────────────────────── */}
        {isMusicActive && (
          <div className="np-music">
            <div
              className="np-music-bg"
              style={{ backgroundImage: `url(/api/music/cover/${song.albumId || song.id}?size=300)` }}
            />

            {/* Player column */}
            <div className="np-music-main">
              <img
                src={`/api/music/cover/${song.albumId || song.id}?size=300`}
                className="np-album-art"
                alt="Album art"
                onError={(e: any) => { e.target.style.display = 'none'; }}
              />

              <div className="np-song-info">
                <div className="np-song-title">{song.title}</div>
                <div className="np-song-artist">{song.artist}</div>
              </div>

              {/* Progress */}
              <div className="np-progress-wrap">
                <span className="np-time">{fmt(localPos)}</span>
                <input
                  type="range"
                  className="np-progress-bar"
                  min={0} max={duration || 1} step={1}
                  value={localPos}
                  onMouseDown={onSeekStart}
                  onTouchStart={onSeekStart}
                  onChange={onSeekChange}
                  onMouseUp={onSeekEnd}
                  onTouchEnd={onSeekEnd}
                  style={{ '--pct': `${progress}%` } as React.CSSProperties}
                />
                <span className="np-time np-time--remaining">-{fmt(Math.max(0, duration - localPos))}</span>
              </div>

              {/* Transport */}
              <div className="np-transport">
                <button className={`btn-icon np-aux ${music?.shuffle ? 'np-aux--on' : ''}`} onClick={cmdShuffle} title="Shuffle"><Shuffle size={18} /></button>
                <button className="btn-icon np-skip" onClick={cmdPrev} title="Previous"><SkipBack size={26} fill="currentColor" /></button>
                <button className="btn-icon np-playpause" onClick={cmdToggle}>
                  {isPaused ? <Play size={32} fill="currentColor" /> : <Pause size={32} fill="currentColor" />}
                </button>
                <button className="btn-icon np-skip" onClick={cmdNext} title="Next"><SkipForward size={26} fill="currentColor" /></button>
                <button className={`btn-icon np-aux ${music?.repeat !== 'off' ? 'np-aux--on' : ''}`} onClick={cmdRepeat} title="Repeat"><RepeatIcon size={18} /></button>
              </div>

              {/* Like + Stop row */}
              <div className="np-extra-row">
                <button className={`btn-icon np-heart ${isLiked ? 'np-heart--on' : ''}`} onClick={toggleLike} title={isLiked ? 'Remove from Liked' : 'Add to Liked'}>
                  <Heart size={20} fill={isLiked ? 'var(--neon-green)' : 'none'} />
                </button>
                <button className="btn-icon np-stop-music" onClick={cmdStop} title="Stop & clear queue">
                  <Power size={18} />
                </button>
              </div>
            </div>
            
            {/* Queue panel */}
            <div className="np-queue-panel">
              <div className="np-queue-header">
                <span>Queue</span>
                <span className="np-queue-count">{queue.length} tracks</span>
              </div>
              <div className="np-queue-list">
                {queue.map((t, i) => {
                  const isActive = i === queueIdx;
                  return (
                    <div
                      key={`${t.id}-${i}`}
                      className={`np-queue-item ${isActive ? 'active' : ''}`}
                      onClick={() => !isActive && cmdJump(i)}
                    >
                      <div className="np-queue-num">
                        {isActive && !isPaused ? <EqBars /> : (isActive ? <Pause size={12} /> : i + 1)}
                      </div>
                      <div className="np-queue-info">
                        <div className="np-queue-title">{t.title}</div>
                        <div className="np-queue-artist">{t.artist}</div>
                      </div>
                      <span className="np-queue-dur">{fmt(t.duration)}</span>
                      {!isActive && (
                        <button
                          className="btn-icon np-queue-rm"
                          onClick={e => { e.stopPropagation(); cmdRemoveFromQueue(i); }}
                          title="Remove"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  );
                })}
                {queue.length === 0 && <div className="np-queue-empty">Queue is empty</div>}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Global Controls Sidebar ──────────────────────────── */}
      <aside className="np-sidebar glass-panel">
        <h3 className="np-sidebar-title">Global Controls</h3>

        <div className="np-control-group">
          <div className="np-cg-label"><Volume2 size={14}/> Volume</div>
          <div className="np-vol-row">
            <button className="btn-icon" onClick={toggleMute}>
              {isMuted || vol === 0 ? <VolumeX size={18} color="var(--text-secondary)" /> : <Volume2 size={18} color="var(--text-secondary)" />}
            </button>
            <input type="range" className="volume-slider np-vol-slider" min={0} max={100} value={vol} onChange={handleVol} />
            <span className="np-vol-label">{isMuted ? 0 : vol}%</span>
          </div>
        </div>

        {(isPlayingGame || isPlayingYoutube) && levels.length > 0 && (
          <div className="np-control-group">
            <div className="np-cg-label"><Monitor size={14}/> Quality</div>
            <select className="np-select" onChange={e => sendPlayerCommand({ command: 'quality', level: parseInt(e.target.value) })}>
              <option value="-1">Auto</option>
              {levels.map((lvl: any, i: number) => (
                <option key={i} value={i}>{lvl.height ? `${lvl.height}p` : `Level ${i}`} {lvl.bitrate ? `(${Math.round(lvl.bitrate/1000)}k)` : ''}</option>
              ))}
            </select>
          </div>
        )}

        {(isPlayingGame || isPlayingYoutube) && (
          <div className="np-control-group">
            <div className="np-cg-label"><Subtitles size={14}/> Captions</div>
            <div className="np-switch-row">
              <span className="np-switch-text">Enable Subtitles</span>
              <label className="switch">
                <input type="checkbox" checked={ccEnabled} onChange={e => { setCcEnabled(e.target.checked); sendPlayerCommand({ command: 'captions', enabled: e.target.checked }); }} />
                <span className="slider"></span>
              </label>
            </div>
          </div>
        )}

        {isPlayingGame && (
          <div className="np-control-group">
            <div className="np-cg-label"><Layers size={14}/> Overlays</div>
            <div className="np-switch-row">
              <span className="np-switch-text">Strike Zone</span>
              <label className="switch"><input type="checkbox" checked={!!settings.strike_zone_enabled} onChange={e => updateSetting({ strike_zone_enabled: e.target.checked })} /><span className="slider"></span></label>
            </div>
            <div className="np-switch-row" style={{ marginTop: '8px' }}>
              <span className="np-switch-text">Batter Intel</span>
              <label className="switch"><input type="checkbox" checked={!!settings.batter_intel_enabled} onChange={e => updateSetting({ batter_intel_enabled: e.target.checked })} /><span className="slider"></span></label>
            </div>
            <div className="np-switch-row" style={{ marginTop: '8px' }}>
              <span className="np-switch-text">Innings Breaks</span>
              <label className="switch"><input type="checkbox" checked={!!settings.between_innings_enabled} onChange={e => updateSetting({ between_innings_enabled: e.target.checked })} /><span className="slider"></span></label>
            </div>
          </div>
        )}

        {cec.available && (
          <div className="np-control-group" style={{ marginTop: 'auto' }}>
            <div className="np-cg-label"><Power size={14}/> CEC TV Power</div>
            <div className="np-btn-row">
              <button className="btn" onClick={() => toggleCec('on')}>Turn ON</button>
              <button className="btn" onClick={() => toggleCec('off')}>Turn OFF</button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
};

export default NowPlaying;
