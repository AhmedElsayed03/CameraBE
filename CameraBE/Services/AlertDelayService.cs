using CameraBE.Hubs;
using Microsoft.AspNetCore.SignalR;
using Microsoft.Extensions.Options;

namespace CameraBE.Services
{
    /// <summary>
    /// Background service that enforces a sustained-alert delay before broadcasting
    /// via SignalR.
    ///
    /// Logic:
    ///   - When Python posts an alert, the controller calls RegisterAlert() instead of
    ///     broadcasting immediately.
    ///   - This service tracks a "streak" per (CameraId, Type).
    ///   - If consecutive alerts keep arriving without a gap larger than
    ///     HeartbeatTimeoutSeconds, the streak stays alive.
    ///   - Once the streak has been alive for SignalRDelaySeconds continuously, the
    ///     alert is broadcast to the frontend exactly once for that streak.
    ///   - If the streak is broken (gap > HeartbeatTimeoutSeconds) it is discarded.
    ///     A fresh alert starts a new streak from zero.
    /// </summary>
    public class AlertDelayService : BackgroundService
    {
        private readonly IHubContext<AlertHub> _hubContext;
        private readonly AlertSettings _settings;
        private readonly ILogger<AlertDelayService> _logger;

        private readonly object _lock = new();

        // Key: (CameraId, AlertType)
        private readonly Dictionary<(int, string), AlertStreak> _streaks = new();

        public AlertDelayService(
            IHubContext<AlertHub> hubContext,
            IOptions<AlertSettings> settings,
            ILogger<AlertDelayService> logger)
        {
            _hubContext = hubContext;
            _settings = settings.Value;
            _logger = logger;
        }

        /// <summary>
        /// Called by the controller every time Python sends an alert.
        /// The alert is already saved in the DB before this is called.
        /// </summary>
        public void RegisterAlert(int cameraId, string type, int alertId, string cameraName, string severity)
        {
            var now = DateTime.UtcNow;
            var key = (cameraId, type);

            lock (_lock)
            {
                if (_streaks.TryGetValue(key, out var existing))
                {
                    var gapSeconds = (now - existing.LastSeenAt).TotalSeconds;

                    if (gapSeconds > _settings.HeartbeatTimeoutSeconds)
                    {
                        // Gap is too large — the condition was gone and came back.
                        // Discard the old streak and start fresh.
                        _logger.LogDebug(
                            "Alert streak for camera {CameraId}/{Type} reset after {Gap:F1}s gap.",
                            cameraId, type, gapSeconds);

                        _streaks[key] = new AlertStreak
                        {
                            FirstSeenAt = now,
                            LastSeenAt = now,
                            LastAlertId = alertId,
                            CameraName = cameraName,
                            Severity = severity,
                            Broadcasted = false
                        };
                    }
                    else
                    {
                        // Streak continues — just update the heartbeat and latest data.
                        existing.LastSeenAt = now;
                        existing.LastAlertId = alertId;
                        existing.CameraName = cameraName;
                        existing.Severity = severity;
                    }
                }
                else
                {
                    // New streak.
                    _streaks[key] = new AlertStreak
                    {
                        FirstSeenAt = now,
                        LastSeenAt = now,
                        LastAlertId = alertId,
                        CameraName = cameraName,
                        Severity = severity,
                        Broadcasted = false
                    };

                    _logger.LogDebug(
                        "New alert streak started for camera {CameraId}/{Type}.",
                        cameraId, type);
                }
            }
        }

        protected override async Task ExecuteAsync(CancellationToken stoppingToken)
        {
            _logger.LogInformation(
                "AlertDelayService started. Delay={Delay}s, HeartbeatTimeout={HB}s.",
                _settings.SignalRDelaySeconds, _settings.HeartbeatTimeoutSeconds);

            while (!stoppingToken.IsCancellationRequested)
            {
                await Task.Delay(500, stoppingToken);

                var now = DateTime.UtcNow;
                List<((int, string) key, AlertStreak streak)>? toProcess = null;

                lock (_lock)
                {
                    var expired = new List<(int, string)>();

                    foreach (var kvp in _streaks)
                    {
                        var streak = kvp.Value;
                        var gapSeconds = (now - streak.LastSeenAt).TotalSeconds;

                        if (gapSeconds > _settings.HeartbeatTimeoutSeconds)
                        {
                            // Streak expired without reaching the delay threshold — discard.
                            _logger.LogDebug(
                                "Alert streak for camera {CameraId}/{Type} expired without broadcast.",
                                kvp.Key.Item1, kvp.Key.Item2);
                            expired.Add(kvp.Key);
                            continue;
                        }

                        var durationSeconds = (now - streak.FirstSeenAt).TotalSeconds;

                        if (!streak.Broadcasted && durationSeconds >= _settings.SignalRDelaySeconds)
                        {
                            // Sustained for the required delay — queue for broadcast.
                            streak.Broadcasted = true;
                            toProcess ??= new();
                            toProcess.Add((kvp.Key, streak));
                        }
                    }

                    foreach (var key in expired)
                        _streaks.Remove(key);
                }

                if (toProcess != null)
                {
                    foreach (var (key, streak) in toProcess)
                    {
                        _logger.LogInformation(
                            "Broadcasting sustained alert for camera {CameraId}/{Type} after {Delay}s.",
                            key.Item1, key.Item2, _settings.SignalRDelaySeconds);

                        await _hubContext.Clients.All.SendAsync(
                            "ReceiveAlert",
                            new
                            {
                                Id = streak.LastAlertId,
                                CameraId = key.Item1,
                                CameraName = streak.CameraName,
                                Type = key.Item2,
                                Severity = streak.Severity,
                                Timestamp = streak.LastSeenAt
                            },
                            stoppingToken);
                    }
                }
            }
        }

        private class AlertStreak
        {
            public DateTime FirstSeenAt { get; set; }
            public DateTime LastSeenAt { get; set; }
            public bool Broadcasted { get; set; }
            public int LastAlertId { get; set; }
            public string CameraName { get; set; } = string.Empty;
            public string Severity { get; set; } = string.Empty;
        }
    }
}
