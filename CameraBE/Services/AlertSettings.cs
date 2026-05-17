namespace CameraBE.Services
{
    public class AlertSettings
    {
        /// <summary>
        /// Default delay (seconds) an alert must be continuously received before it is
        /// broadcast to the frontend via SignalR. Applies to all alert types unless
        /// overridden in <see cref="TypeDelayOverrides"/>.
        /// </summary>
        public int SignalRDelaySeconds { get; set; } = 10;

        /// <summary>
        /// Maximum gap (in seconds) between consecutive alert POST calls for the
        /// same camera+type before the streak is considered broken and the timer resets.
        /// </summary>
        public int HeartbeatTimeoutSeconds { get; set; } = 6;


    }
}
