import React, { useRef, useState, useEffect } from 'react';
import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, Power, Tv } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import './NowPlayingBar.css';

const NowPlayingBar: React.FC = React.memo(() => {
  const { status, games, stopPlayback, music, volume } = useTvAutomator();

  const [localVol, setLocalVol] = useState(volume.volume);
  const [localMuted, setLocalMuted] = useState(volume.muted);
  const volTimeout = useRef<any>(null);

  useEffect(() => {
    if (!volTimeout.current) {
      setLocalVol(volume.volume);
      setLocalMuted(volume.muted);
    }
  }, [volume]);

  const isPlayingGame    = !!status.now_playing_game_id;
  const isPlayingYoutube = status.youtube_mode && !isPlayingGame;
  const musicSong        = music.song;
  const isMusicPlaying   = !!(musicSong && music.playing && !isPlayingGame && !isPlayingYoutube);
  const isAnythingPlaying = isPlayingGame || isPlayingYoutube || isMusicPlaying;

  const sendCommand = async (cmd: string) => {
    await fetch('/api/music/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }),
    });
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseInt(e.target.value);
    setLocalVol(val);
    if (volTimeout.current) clearTimeout(volTimeout.current);
    volTimeout.current = setTimeout(() => {
      fetch(`/api/volume?level=${val}`, { method: 'POST' });
      volTimeout.current = null;
    }, 150);
  };

  const toggleMute = () => {
    const newMuted = !localMuted;
    setLocalMuted(newMuted);
    fetch(`/api/volume?mute=${newMuted}`, { method: 'POST' });
  };

  if (!isAnythingPlaying) return null;

  const game = isPlayingGame ? games.find(g => g.game_id === status.now_playing_game_id) : null;

  return (
    <div className="now-playing-bar">
      <div className="npb-info">
        {isPlayingGame && (
          <>
            <div className="npb-icon-box npb-game"><Tv size={20} /></div>
            <div className="npb-text">
              <div className="npb-title">{game ? game.display_matchup : `Game ${status.now_playing_game_id}`}</div>
              <div className="npb-subtitle">{game ? `${game.display_score} · ${game.status_label}` : 'Live on TV'}</div>
            </div>
          </>
        )}

        {isPlayingYoutube && (
          <>
            <div className="npb-icon-box npb-youtube">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
              </svg>
            </div>
            <div className="npb-text">
              <div className="npb-title">YouTube</div>
              <div className="npb-subtitle">Playing on TV</div>
            </div>
          </>
        )}

        {isMusicPlaying && (
          <>
            <img
              src={`/api/music/cover/${musicSong.albumId || musicSong.id}`}
              className="npb-art"
              alt="Album art"
              onError={(e: any) => { e.target.style.display = 'none'; }}
            />
            <div className="npb-text">
              <div className="npb-title">{musicSong.title}</div>
              <div className="npb-subtitle">{musicSong.artist}</div>
            </div>
          </>
        )}
      </div>

      <div className="npb-controls">
        {isMusicPlaying && (
          <>
            <button className="btn-icon" onClick={() => sendCommand('prev')}><SkipBack size={20} /></button>
            <button className="btn-icon npb-play-btn" onClick={() => sendCommand('toggle')}>
              {music.paused ? <Play size={22} fill="currentColor" /> : <Pause size={22} fill="currentColor" />}
            </button>
            <button className="btn-icon" onClick={() => sendCommand('next')}><SkipForward size={20} /></button>
          </>
        )}
        {(isPlayingGame || isPlayingYoutube) && (
          <button className="btn btn-primary npb-stop-btn" onClick={() => stopPlayback()}>
            <Power size={14} /> Stop
          </button>
        )}
      </div>

      <div className="npb-volume">
        <button className="btn-icon" onClick={toggleMute}>
          {localMuted || localVol === 0
            ? <VolumeX size={18} color="var(--text-secondary)" />
            : <Volume2 size={18} color="var(--text-secondary)" />}
        </button>
        <input type="range" className="volume-slider" min="0" max="100" value={localVol} onChange={handleVolumeChange} />
      </div>
    </div>
  );
});

export default NowPlayingBar;
