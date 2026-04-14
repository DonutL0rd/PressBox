import React, { useEffect, useRef, useState } from 'react';
import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, Power, Shuffle, Repeat, Repeat1, Tv, Monitor, Subtitles, Layers } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import './NowPlaying.css';

const fmt = (sec: number) => {
  if (!sec || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
};

const NowPlaying: React.FC = () => {
  const { status, games, stopPlayback } = useTvAutomator();

  const [music, setMusic] = useState<any>(null);
  const [queue, setQueue] = useState<any[]>([]);
  const [queueIdx, setQueueIdx] = useState(-1);
  const [vol, setVol] = useState(50);
  const [isMuted, setIsMuted] = useState(false);
  const volTimeout = useRef<any>(null);

  // Global Controls State
  const [settings, setSettings] = useState<any>({});
  const [levels, setLevels] = useState<any[]>([]);
  const [cec, setCec] = useState<any>({});
  const [ccEnabled, setCcEnabled] = useState(false);

  // Local position ticker for smooth progress bar
  const [localPos, setLocalPos] = useState(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = async () => {
    try {
      const [stRes, qRes, vRes, setRes, lvlRes, cecRes] = await Promise.all([
        fetch('/api/music/status'),
        fetch('/api/music/queue'),
        fetch('/api/volume'),
        fetch('/api/settings'),
        fetch('/api/player/levels'),
        fetch('/api/cec/status'),
      ]);
      if (stRes.ok) {
        const st = await stRes.json();
        setMusic(st);
        setLocalPos(st.position ?? 0);
      }
      if (qRes.ok) {
        const q = await qRes.json();
        setQueue(q.queue || []);
        setQueueIdx(q.index ?? -1);
      }
      if (vRes.ok) {
        const v = await vRes.json();
        setVol(v.volume);
        setIsMuted(v.muted);
      }
      if (setRes.ok) setSettings(await setRes.json());
      if (lvlRes.ok) setLevels((await lvlRes.json()).levels || []);
      if (cecRes.ok) setCec(await cecRes.json());
    } catch {}
  };

  useEffect(() => {
    fetchAll();
    const iv = setInterval(fetchAll, 3000);
    return () => clearInterval(iv);
  }, []);

  // Tick position forward locally for smooth progress
  useEffect(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (music?.playing && !music?.paused) {
      tickRef.current = setInterval(() => {
        setLocalPos(p => Math.min(p + 1, music?.duration ?? p));
      }, 1000);
    }
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [music?.playing, music?.paused, music?.duration]);

  const cmdMusic = async (command: string) => {
    await fetch('/api/music/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    });
    fetchAll();
  };

  const seekMusic = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const pos = parseFloat(e.target.value);
    setLocalPos(pos);
    await fetch('/api/music/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'seek', position: pos }),
    });
  };

  const handleVol = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseInt(e.target.value);
    setVol(val);
    if (volTimeout.current) clearTimeout(volTimeout.current);
    volTimeout.current = setTimeout(() => {
      fetch(`/api/volume?level=${val}`, { method: 'POST' });
    }, 150);
  };

  const toggleMute = () => {
    fetch(`/api/volume?mute=${!isMuted}`, { method: 'POST' }).then(fetchAll);
  };

  const updateSetting = async (payload: any) => {
    setSettings((prev: any) => ({ ...prev, ...payload }));
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  };

  const sendPlayerCommand = async (cmd: any) => {
    await fetch('/api/player/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cmd)
    });
  };

  const toggleCec = async (action: 'on' | 'off') => {
    await fetch(`/api/cec/${action}`, { method: 'POST' });
    fetchAll();
  };

  const isPlayingGame    = !!status.now_playing_game_id;
  const isPlayingYoutube = status.youtube_mode && !isPlayingGame;
  const song             = music?.song;
  const isMusicActive    = !!(song && !isPlayingGame && !isPlayingYoutube);
  const isIdle           = !isPlayingGame && !isPlayingYoutube && !isMusicActive;

  const game = isPlayingGame ? games.find(g => g.game_id === status.now_playing_game_id) : null;

  const duration = music?.duration ?? 0;
  const progress = duration > 0 ? (localPos / duration) * 100 : 0;
  const RepeatIcon = music?.repeat === 'one' ? Repeat1 : Repeat;

  return (
    <div className="np-page animate-in">
      <div className="np-main-content">
        {/* ── Idle ──────────────────────────────────────────── */}
        {isIdle && (
          <div className="np-idle">
            <div className="np-idle-icon">
              <Play size={48} />
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

        {/* ── Music ─────────────────────────────────────────── */}
        {isMusicActive && (
          <div className="np-music">
            <div
              className="np-music-bg"
              style={{ backgroundImage: `url(/api/music/cover/${song.albumId || song.id}?size=300)` }}
            />
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
              <div className="np-progress-wrap">
                <span className="np-time">{fmt(localPos)}</span>
                <input
                  type="range"
                  className="np-progress-bar"
                  min={0} max={duration || 1} step={1}
                  value={localPos} onChange={seekMusic}
                  style={{ '--pct': `${progress}%` } as React.CSSProperties}
                />
                <span className="np-time">{fmt(duration)}</span>
              </div>
              <div className="np-transport">
                <button className={`btn-icon np-aux ${music?.shuffle ? 'np-aux--on' : ''}`} onClick={() => cmdMusic('shuffle')} title="Shuffle"><Shuffle size={18} /></button>
                <button className="btn-icon np-skip" onClick={() => cmdMusic('prev')}><SkipBack size={26} fill="currentColor" /></button>
                <button className="btn-icon np-playpause" onClick={() => cmdMusic('toggle')}>
                  {music?.paused ? <Play size={32} fill="currentColor" /> : <Pause size={32} fill="currentColor" />}
                </button>
                <button className="btn-icon np-skip" onClick={() => cmdMusic('next')}><SkipForward size={26} fill="currentColor" /></button>
                <button className={`btn-icon np-aux ${music?.repeat !== 'off' ? 'np-aux--on' : ''}`} onClick={() => cmdMusic('repeat')} title="Repeat"><RepeatIcon size={18} /></button>
              </div>
            </div>
            
            {/* Music Queue merged into main content side */}
            <div className="np-queue-panel">
              <div className="np-queue-header">
                <span>Up Next</span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>{queue.length} tracks</span>
              </div>
              <div className="np-queue-list">
                {queue.map((t, i) => (
                  <div key={`${t.id}-${i}`} className={`np-queue-item ${i === queueIdx ? 'active' : ''}`}>
                    <div className="np-queue-num">{i + 1}</div>
                    <div className="np-queue-info">
                      <div className="np-queue-title">{t.title}</div>
                      <div className="np-queue-artist">{t.artist}</div>
                    </div>
                    {i === queueIdx && <span className="live-pulse-dot" style={{ flexShrink: 0 }} />}
                  </div>
                ))}
                {queue.length === 0 && <div className="np-queue-empty">Queue is empty</div>}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Central Universal Controls Bar ──────────────────────────────────── */}
      <aside className="np-sidebar glass-panel">
        <h3 className="np-sidebar-title">Global Controls</h3>

        {/* Universal Volume */}
        <div className="np-control-group">
          <div className="np-cg-label"><Volume2 size={14}/> Volume</div>
          <div className="np-vol-row">
            <button className="btn-icon" onClick={toggleMute}>
              {isMuted || vol === 0 ? <VolumeX size={18} color="var(--text-secondary)" /> : <Volume2 size={18} color="var(--text-secondary)" />}
            </button>
            <input
              type="range"
              className="volume-slider np-vol-slider"
              min={0} max={100}
              value={vol}
              onChange={handleVol}
            />
            <span className="np-vol-label">{isMuted ? 0 : vol}%</span>
          </div>
        </div>

        {/* Streams Controls */}
        {(isPlayingGame || isPlayingYoutube) && levels.length > 0 && (
          <div className="np-control-group">
            <div className="np-cg-label"><Monitor size={14}/> Quality</div>
            <select 
              className="np-select"
              onChange={e => sendPlayerCommand({ command: 'quality', level: parseInt(e.target.value) })}
            >
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
                <input type="checkbox" checked={ccEnabled} onChange={e => {
                  setCcEnabled(e.target.checked);
                  sendPlayerCommand({ command: 'captions', enabled: e.target.checked });
                }} />
                <span className="slider"></span>
              </label>
            </div>
          </div>
        )}

        {/* Overlays (MLB) */}
        {isPlayingGame && (
          <div className="np-control-group">
            <div className="np-cg-label"><Layers size={14}/> Overlays</div>
            <div className="np-switch-row">
              <span className="np-switch-text">Strike Zone</span>
              <label className="switch">
                <input type="checkbox" checked={!!settings.strike_zone_enabled} onChange={e => updateSetting({ strike_zone_enabled: e.target.checked })} />
                <span className="slider"></span>
              </label>
            </div>
            <div className="np-switch-row" style={{ marginTop: '8px' }}>
              <span className="np-switch-text">Batter Intel</span>
              <label className="switch">
                <input type="checkbox" checked={!!settings.batter_intel_enabled} onChange={e => updateSetting({ batter_intel_enabled: e.target.checked })} />
                <span className="slider"></span>
              </label>
            </div>
            <div className="np-switch-row" style={{ marginTop: '8px' }}>
              <span className="np-switch-text">Innings Breaks</span>
              <label className="switch">
                <input type="checkbox" checked={!!settings.between_innings_enabled} onChange={e => updateSetting({ between_innings_enabled: e.target.checked })} />
                <span className="slider"></span>
              </label>
            </div>
          </div>
        )}

        {/* TV Power */}
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
