import React, { useEffect, useState, useCallback } from 'react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import { Play, Clock, Trash2, History, Radio, Video } from 'lucide-react';
import './YouTube.css';

// ── Helpers ──────────────────────────────────────────────────────

const timeAgo = (iso: string): string => {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

const fmtDuration = (sec: number): string => {
  if (!sec || sec <= 0) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${s}` : `${m}:${s}`;
};

// ── Types ────────────────────────────────────────────────────────

interface HistoryItem {
  video_id: string;
  title: string;
  channel: string;
  thumbnail: string;
  duration: number;
  position: number;
  completed: boolean;
  last_watched: string;
}

interface SuggestedVideo {
  video_id: string;
  title: string;
  published: string;
  thumbnail: string;
  channel: string;
}

// ── Skeleton placeholder ─────────────────────────────────────────

const SkeletonCards: React.FC<{ count: number }> = ({ count }) => (
  <div className="yt-loading">
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="yt-skeleton">
        <div className="yt-skeleton-thumb" />
        <div className="yt-skeleton-info">
          <div className="yt-skeleton-line" />
          <div className="yt-skeleton-line" />
        </div>
      </div>
    ))}
  </div>
);

// ── Video Card ───────────────────────────────────────────────────

const VideoCard: React.FC<{
  videoId: string;
  title: string;
  thumbnail: string;
  channel?: string;
  time?: string;
  duration?: number;
  position?: number;
  completed?: boolean;
  onPlay: (id: string, resume?: number) => void;
  onDelete?: (id: string) => void;
}> = ({ videoId, title, thumbnail, channel, time, duration, position, completed, onPlay, onDelete }) => {
  const progress = duration && duration > 0 && position ? Math.min((position / duration) * 100, 100) : 0;
  const hasProgress = progress > 0 && !completed;

  return (
    <div
      className="yt-card"
      onClick={() => onPlay(videoId, hasProgress ? position : 0)}
      role="button"
      tabIndex={0}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPlay(videoId, hasProgress ? position : 0); } }}
    >
      {/* Thumbnail */}
      <div className="yt-card-thumb">
        {thumbnail ? (
          <img src={thumbnail} alt={title} loading="lazy" />
        ) : (
          <div className="yt-thumb-fallback">
            <Video size={36} color="var(--text-tertiary)" />
          </div>
        )}

        {/* Play button overlay */}
        <div className="yt-thumb-play">
          <Play size={22} fill="#fff" color="#fff" />
        </div>

        {/* Duration badge */}
        {!!duration && duration > 0 && (
          <div className="yt-duration-badge">{fmtDuration(duration)}</div>
        )}

        {/* Progress bar */}
        {(hasProgress || completed) && (
          <div className="yt-progress-bar">
            <div
              className={`yt-progress-fill ${completed ? 'completed' : ''}`}
              style={{ width: completed ? '100%' : `${progress}%` }}
            />
          </div>
        )}
      </div>

      {/* Delete button */}
      {onDelete && (
        <button
          className="yt-card-delete"
          onClick={e => { e.stopPropagation(); onDelete(videoId); }}
          title="Remove from history"
        >
          <Trash2 size={14} />
        </button>
      )}

      {/* Info */}
      <div className="yt-card-info">
        <div className="yt-card-title">{title || `Video ${videoId}`}</div>
        {channel && <div className="yt-card-channel">{channel}</div>}
        <div className="yt-card-meta">
          {time && <span className="yt-card-time">{time}</span>}
          {hasProgress && <span className="yt-resume-tag">Resume {fmtDuration(position!)}</span>}
          {completed && <span className="yt-completed-tag">Watched</span>}
        </div>
      </div>
    </div>
  );
};

// ── Main YouTube Component ───────────────────────────────────────

const YouTube: React.FC = () => {
  const { playYoutube } = useTvAutomator();
  const [url, setUrl] = useState('');

  // Data state
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [suggested, setSuggested] = useState<Record<string, SuggestedVideo[]>>({});
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [loadingSuggested, setLoadingSuggested] = useState(true);

  // ── Fetch data ─────────────────────────────────────────────────

  const fetchHistory = useCallback(async () => {
    try {
      const r = await fetch('/api/youtube/history');
      if (r.ok) {
        const data = await r.json();
        setHistory(Array.isArray(data) ? data : []);
      }
    } catch {} finally {
      setLoadingHistory(false);
    }
  }, []);

  const fetchSuggested = useCallback(async () => {
    try {
      const r = await fetch('/api/youtube/suggested');
      if (r.ok) {
        const data = await r.json();
        setSuggested(typeof data === 'object' && data !== null ? data : {});
      }
    } catch {} finally {
      setLoadingSuggested(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
    fetchSuggested();
  }, [fetchHistory, fetchSuggested]);

  // ── Actions ────────────────────────────────────────────────────

  const handleCast = (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    playYoutube(url.trim());
    setUrl('');
  };

  const handlePlay = (videoId: string, resumePosition?: number) => {
    const ytUrl = `https://www.youtube.com/watch?v=${videoId}`;
    if (resumePosition && resumePosition > 5) {
      // Use the backend's resume support
      fetch('/api/youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: ytUrl, resume_position: resumePosition }),
      }).catch(() => {});
    } else {
      playYoutube(ytUrl);
    }
  };

  const handleDelete = async (videoId: string) => {
    setHistory(prev => prev.filter(h => h.video_id !== videoId));
    try {
      await fetch(`/api/youtube/history/${videoId}`, { method: 'DELETE' });
    } catch {}
  };

  // ── Partition history ──────────────────────────────────────────

  const continueWatching = history.filter(h => !h.completed && h.position > 5);
  const recentlyWatched = history.filter(h => h.completed || h.position <= 5);

  // ── Channel keys ───────────────────────────────────────────────

  const channelNames = Object.keys(suggested);

  return (
    <div className="yt-page animate-in">
      {/* ── URL Input Bar ──────────────────────────────────────── */}
      <form className="yt-url-bar" onSubmit={handleCast}>
        <div className="yt-url-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="var(--neon-red)">
            <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
          </svg>
        </div>
        <input
          type="text"
          className="yt-url-input"
          placeholder="Paste a YouTube URL to cast to TV…"
          value={url}
          onChange={e => setUrl(e.target.value)}
          onPaste={e => {
            const pasted = e.clipboardData.getData('text').trim();
            if (pasted && (pasted.includes('youtube.com') || pasted.includes('youtu.be'))) {
              e.preventDefault();
              playYoutube(pasted);
              setUrl('');
            }
          }}
        />
        <button type="submit" className="btn-yt-cast" disabled={!url.trim()}>
          <Play size={16} /> Cast to TV
        </button>
      </form>

      {/* ── Scrollable Body ────────────────────────────────────── */}
      <div className="yt-scroll-body">

        {/* ── Continue Watching ─────────────────────────────────── */}
        {continueWatching.length > 0 && (
          <section className="yt-section">
            <div className="yt-section-head">
              <h2 className="yt-section-title">
                <Play size={18} color="var(--neon-red)" /> Continue Watching
              </h2>
              <span className="yt-section-count">{continueWatching.length}</span>
            </div>
            <div className="yt-grid">
              {continueWatching.map(h => (
                <VideoCard
                  key={h.video_id}
                  videoId={h.video_id}
                  title={h.title}
                  thumbnail={h.thumbnail}
                  channel={h.channel}
                  time={timeAgo(h.last_watched)}
                  duration={h.duration}
                  position={h.position}
                  completed={false}
                  onPlay={handlePlay}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          </section>
        )}

        {/* ── Recently Watched ──────────────────────────────────── */}
        {recentlyWatched.length > 0 && (
          <section className="yt-section">
            <div className="yt-section-head">
              <h2 className="yt-section-title">
                <History size={18} color="var(--text-secondary)" /> Watch History
              </h2>
              <span className="yt-section-count">{recentlyWatched.length}</span>
            </div>
            <div className="yt-grid">
              {recentlyWatched.map(h => (
                <VideoCard
                  key={h.video_id}
                  videoId={h.video_id}
                  title={h.title}
                  thumbnail={h.thumbnail}
                  channel={h.channel}
                  time={timeAgo(h.last_watched)}
                  duration={h.duration}
                  position={h.position}
                  completed={h.completed}
                  onPlay={handlePlay}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          </section>
        )}

        {/* History loading state */}
        {loadingHistory && history.length === 0 && (
          <section className="yt-section">
            <h2 className="yt-section-title">
              <Clock size={18} color="var(--text-secondary)" /> Loading History…
            </h2>
            <SkeletonCards count={4} />
          </section>
        )}

        {/* ── Suggested Channels ─────────────────────────────────── */}
        {channelNames.length > 0 && (
          <section className="yt-section">
            <div className="yt-section-head">
              <h2 className="yt-section-title">
                <Radio size={18} color="var(--neon-cyan)" /> Suggested Channels
              </h2>
            </div>

            {channelNames.map(channelName => {
              const videos = suggested[channelName];
              if (!Array.isArray(videos) || videos.length === 0) return null;
              return (
                <div key={channelName} className="yt-channel-section">
                  <h3 className="yt-channel-name">
                    <span className="channel-dot" />
                    {channelName}
                  </h3>
                  <div className="yt-channel-row">
                    {videos.map(v => (
                      <VideoCard
                        key={v.video_id}
                        videoId={v.video_id}
                        title={v.title}
                        thumbnail={v.thumbnail}
                        channel={v.channel}
                        time={timeAgo(v.published)}
                        onPlay={handlePlay}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </section>
        )}

        {/* Suggested loading state */}
        {loadingSuggested && channelNames.length === 0 && (
          <section className="yt-section">
            <h2 className="yt-section-title">
              <Radio size={18} color="var(--neon-cyan)" /> Loading Suggestions…
            </h2>
            <SkeletonCards count={6} />
          </section>
        )}

        {/* Empty state — no history, no suggestions */}
        {!loadingHistory && !loadingSuggested && history.length === 0 && channelNames.length === 0 && (
          <div className="yt-empty">
            <div className="yt-empty-icon">
              <Video size={28} color="var(--neon-red)" />
            </div>
            <p>No watch history yet. Paste a YouTube URL above to cast your first video to the TV.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default YouTube;
