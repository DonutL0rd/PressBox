import React, { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

// Type definitions based on the FastAPI backend
export interface Team {
  name: string;
  abbreviation: string;
  score: number | null;
}

export interface Game {
  game_id: string;
  provider: string;
  away_team: Team;
  home_team: Team;
  start_time: string;
  display_time: string;
  display_matchup: string;
  display_score: string;
  status: string; // 'scheduled', 'pre_game', 'live', 'final', 'postponed'
  status_label: string;
  is_watchable: boolean;
  venue: string;
  extra: any;
}

export interface Status {
  now_playing_game_id: string | null;
  youtube_mode: boolean;
  authenticated: boolean;
  browser_running: boolean;
  heartbeat_active: boolean;
}

interface TvAutomatorContextType {
  games: Game[];
  status: Status;
  connected: boolean;
  playGame: (gameId: string, feed?: string) => Promise<void>;
  stopPlayback: () => Promise<void>;
  playYoutube: (url: string) => Promise<void>;
  refreshStatus: () => Promise<void>;
  refreshGames: () => Promise<void>;
}

const defaultStatus: Status = {
  now_playing_game_id: null,
  youtube_mode: false,
  authenticated: false,
  browser_running: false,
  heartbeat_active: false,
};

const TvAutomatorContext = createContext<TvAutomatorContextType | null>(null);

export const TvAutomatorProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [games, setGames] = useState<Game[]>([]);
  const [status, setStatus] = useState<Status>(defaultStatus);
  const [connected, setConnected] = useState<boolean>(false);

  const refreshStatus = async () => {
    try {
      const r = await fetch('/api/status');
      if (!r.ok) return;
      const data = await r.json();
      console.log('[TvAutomator] /api/status →', data);
      setStatus(prev => ({ ...prev, ...data }));
    } catch (e) {
      console.error('refreshStatus failed', e);
    }
  };

  const refreshGames = async () => {
    try {
      const r = await fetch('/api/games');
      if (!r.ok) return;
      const data = await r.json();
      console.log('[TvAutomator] /api/games →', Array.isArray(data) ? `${data.length} games` : data);
      if (Array.isArray(data)) setGames(data);
    } catch (e) {
      console.error('refreshGames failed', e);
    }
  };

  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let pollTimer: ReturnType<typeof setInterval>;
    let wsFailCount = 0;
    const MAX_RECONNECT_DELAY = 30000;

    // Belt-and-braces: REST-fetch on mount as a source of truth
    // independent of the WebSocket.
    refreshStatus();
    refreshGames();

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        if (wsFailCount > 0) {
          console.log('[TvAutomator] WebSocket reconnected');
        }
        wsFailCount = 0;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'status') {
            console.log('[TvAutomator] WS status →', data);
            setStatus(prev => ({ ...prev, ...data }));
          } else if (data.type === 'games') {
            if (Array.isArray(data.games)) {
              setGames(data.games);
            }
          }
        } catch (e) {
          console.error('Failed to parse WS message', e);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        const delay = Math.min(3000 * Math.pow(1.5, wsFailCount), MAX_RECONNECT_DELAY);
        reconnectTimer = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        wsFailCount++;
        if (wsFailCount === 1) {
          console.warn('[TvAutomator] WebSocket connection failed — falling back to REST polling');
        }
        ws.close();
      };
    };

    connect();

    // Fallback poll — if the WS ever silently stops delivering, this keeps
    // status + games fresh. 15s is cheap and imperceptible.
    pollTimer = setInterval(() => {
      refreshStatus();
      refreshGames();
    }, 15000);

    return () => {
      clearTimeout(reconnectTimer);
      clearInterval(pollTimer);
      if (ws) ws.close();
    };
  }, []);

  const playGame = async (gameId: string, feed: string = 'HOME') => {
    try {
      await fetch(`/api/play/${gameId}?feed=${feed}`, { method: 'POST' });
    } catch (e) {
      console.error('Play game error:', e);
    }
  };

  const stopPlayback = async () => {
    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch (e) {
      console.error('Stop playback error:', e);
    }
  };

  const playYoutube = async (url: string) => {
    try {
      await fetch('/api/youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
    } catch (e) {
      console.error('YouTube play error:', e);
    }
  };

  return (
    <TvAutomatorContext.Provider value={{ games, status, connected, playGame, stopPlayback, playYoutube, refreshStatus, refreshGames }}>
      {children}
    </TvAutomatorContext.Provider>
  );
};

export const useTvAutomator = () => {
  const ctx = useContext(TvAutomatorContext);
  if (!ctx) {
    throw new Error('useTvAutomator must be used within a TvAutomatorProvider');
  }
  return ctx;
};
