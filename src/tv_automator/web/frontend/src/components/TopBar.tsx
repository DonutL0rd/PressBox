import React from 'react';
import { Power, CheckCircle, XCircle } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';

const TopBar: React.FC = () => {
  const { status, games, stopPlayback } = useTvAutomator();

  const isPlayingGame = !!status.now_playing_game_id;
  const isPlayingYoutube = status.youtube_mode;
  let playingLabel = '';

  if (isPlayingGame) {
    const game = games.find(g => g.game_id === status.now_playing_game_id);
    playingLabel = game ? `Live: ${game.display_matchup}` : `Playing Game ID: ${status.now_playing_game_id}`;
  } else if (isPlayingYoutube) {
    playingLabel = 'Playing YouTube';
  }

  return (
    <div className="top-bar">
      <div className="top-info">
        {(isPlayingGame || isPlayingYoutube) && (
          <div className={`now-playing-pill ${isPlayingYoutube ? 'youtube' : ''}`}>
            <span className="live-pulse-dot" />
            {playingLabel}
          </div>
        )}
      </div>

      <div className="top-controls">
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginRight: '12px' }}>
          {status.authenticated ? (
            <CheckCircle size={14} color="var(--green)" />
          ) : (
            <XCircle size={14} color="var(--text-tertiary)" />
          )}
          <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
            {status.authenticated ? 'Auth OK' : 'No Auth'}
          </span>
        </div>

        {(isPlayingGame || isPlayingYoutube) && (
          <button className="btn" onClick={stopPlayback} style={{ background: 'var(--red)', color: '#fff', border: 'none', fontSize: '0.8rem', padding: '6px 12px' }}>
            <Power size={14} /> Stop
          </button>
        )}
      </div>
    </div>
  );
};

export default TopBar;
