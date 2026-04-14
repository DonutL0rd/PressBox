import React, { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from 'react';

// ── Type definitions ────────────────────────────────────────────

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
  status: string;
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

export interface MusicStatus {
  playing: boolean;
  song: any | null;
  queue_length: number;
  queue_index: number;
  shuffle: boolean;
  repeat: string;
  position: number;
  duration: number;
  paused: boolean;
  volume?: number;
}

export interface VolumeState {
  volume: number;
  muted: boolean;
}

export interface QueueState {
  songs: any[];
  index: number;
}

export interface Settings {
  [key: string]: any;
}

interface TvAutomatorContextType {
  games: Game[];
  status: Status;
  settings: Settings;
  music: MusicStatus;
  volume: VolumeState;
  queue: QueueState;
  connected: boolean;
  playGame: (gameId: string, feed?: string) => Promise<void>;
  stopPlayback: () => Promise<void>;
  playYoutube: (url: string) => Promise<void>;
  refreshStatus: () => Promise<void>;
  refreshGames: () => Promise<void>;
  updateSetting: (payload: any) => Promise<void>;
}

// ── Defaults ────────────────────────────────────────────────────

const defaultStatus: Status = {
  now_playing_game_id: null,
  youtube_mode: false,
  authenticated: false,
  browser_running: false,
  heartbeat_active: false,
};

const defaultMusic: MusicStatus = {
  playing: false,
  song: null,
  queue_length: 0,
  queue_index: -1,
  shuffle: false,
  repeat: 'off',
  position: 0,
  duration: 0,
  paused: true,
};

const defaultVolume: VolumeState = { volume: 50, muted: false };
const defaultQueue: QueueState = { songs: [], index: -1 };

// ── Context ─────────────────────────────────────────────────────

const TvAutomatorContext = createContext<TvAutomatorContextType | null>(null);

export const TvAutomatorProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [games, setGames] = useState<Game[]>([]);
  const [status, setStatus] = useState<Status>(defaultStatus);
  const [settings, setSettings] = useState<Settings>({});
  const [music, setMusic] = useState<MusicStatus>(defaultMusic);
  const [volume, setVolume] = useState<VolumeState>(defaultVolume);
  const [queue, setQueue] = useState<QueueState>(defaultQueue);
  const [connected, setConnected] = useState(false);

  // Track if user is actively changing volume (skip WS updates during drag)
  const volLockRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/status');
      if (r.ok) {
        const data = await r.json();
        setStatus(prev => ({ ...prev, ...data }));
      }
    } catch {}
  }, []);

  const refreshGames = useCallback(async () => {
    try {
      const r = await fetch('/api/games');
      if (r.ok) {
        const data = await r.json();
        if (Array.isArray(data)) setGames(data);
      }
    } catch {}
  }, []);

  const updateSetting = useCallback(async (payload: any) => {
    // Optimistic update
    setSettings(prev => ({ ...prev, ...payload }));
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (r.ok) {
        // Server broadcasts the canonical state via WS, which will update us
      }
    } catch {}
  }, []);

  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let wsFailCount = 0;
    const MAX_RECONNECT_DELAY = 30000;

    // Initial REST fetch as safety net
    refreshStatus();
    refreshGames();

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

      ws.onopen = () => {
        wsFailCount = 0;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case 'status': {
              const { type: _, ...rest } = data;
              setStatus(prev => ({ ...prev, ...rest }));
              break;
            }
            case 'games':
              if (Array.isArray(data.games)) setGames(data.games);
              break;
            case 'settings': {
              const { type: _, ...rest } = data;
              setSettings(rest);
              break;
            }
            case 'music': {
              const { type: _, ...rest } = data;
              setMusic(prev => ({ ...prev, ...rest }));
              break;
            }
            case 'volume':
              // Skip if user is actively dragging volume slider
              if (!volLockRef.current) {
                setVolume({ volume: data.volume, muted: data.muted });
              }
              break;
            case 'queue':
              setQueue({ songs: data.songs || [], index: data.index ?? -1 });
              break;
            case 'autoplay':
              // Handled by views that need it
              break;
          }
        } catch {}
      };

      ws.onclose = () => {
        setConnected(false);
        const delay = Math.min(3000 * Math.pow(1.5, wsFailCount), MAX_RECONNECT_DELAY);
        reconnectTimer = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        wsFailCount++;
        ws.close();
      };
    };

    connect();

    // Very infrequent fallback poll — 60s just to catch any edge cases
    const fallbackPoll = setInterval(() => {
      refreshStatus();
      refreshGames();
    }, 60000);

    return () => {
      clearTimeout(reconnectTimer);
      clearInterval(fallbackPoll);
      if (ws) ws.close();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const playGame = useCallback(async (gameId: string, feed: string = 'HOME') => {
    try {
      await fetch(`/api/play/${gameId}?feed=${feed}`, { method: 'POST' });
    } catch {}
  }, []);

  const stopPlayback = useCallback(async () => {
    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch {}
  }, []);

  const playYoutube = useCallback(async (url: string) => {
    try {
      await fetch('/api/youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
    } catch {}
  }, []);

  return (
    <TvAutomatorContext.Provider value={{
      games, status, settings, music, volume, queue, connected,
      playGame, stopPlayback, playYoutube, refreshStatus, refreshGames, updateSetting,
    }}>
      {children}
    </TvAutomatorContext.Provider>
  );
};

export const useTvAutomator = () => {
  const ctx = useContext(TvAutomatorContext);
  if (!ctx) throw new Error('useTvAutomator must be used within a TvAutomatorProvider');
  return ctx;
};
