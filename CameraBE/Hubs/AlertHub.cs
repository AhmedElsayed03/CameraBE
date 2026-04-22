using Microsoft.AspNetCore.SignalR;

namespace CameraBE.Hubs
{
    /// <summary>
    /// SignalR hub for pushing real-time alert notifications to connected clients.
    /// Clients subscribe to "ReceiveAlert" to get notified when a new alert is created.
    /// </summary>
    public class AlertHub : Hub
    {
        public override async Task OnConnectedAsync()
        {
            await base.OnConnectedAsync();
        }

        public override async Task OnDisconnectedAsync(Exception? exception)
        {
            await base.OnDisconnectedAsync(exception);
        }
    }
}
