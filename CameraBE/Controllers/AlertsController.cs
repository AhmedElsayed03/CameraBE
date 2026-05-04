using CameraBE.Data;
using CameraBE.Entities;
using CameraBE.Hubs;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;

namespace CameraBE.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class AlertsController : ControllerBase
    {
        private readonly AppDbContext _db;
        private readonly IHubContext<AlertHub> _hubContext;

        public AlertsController(AppDbContext db, IHubContext<AlertHub> hubContext)
        {
            _db = db;
            _hubContext = hubContext;
        }

        /// <summary>
        /// GET /api/alerts — Returns all alerts ordered by most recent first.
        /// </summary>
        [HttpGet]
        public async Task<ActionResult<IEnumerable<AlertDto>>> GetAlerts()
        {
            return await _db.Alerts
                .OrderByDescending(a => a.Timestamp)
                .Select(a => new AlertDto
                {
                    Id = a.Id,
                    CameraId = a.CameraId,
                    CameraName = a.Camera != null ? a.Camera.Name : null,
                    Type = a.Type,
                    Severity = a.Severity,
                    Timestamp = a.Timestamp,
                    CreatedAt = a.CreatedAt
                })
                .ToListAsync();
        }

        /// <summary>
        /// POST /api/alerts — Creates an alert, saves to DB, and broadcasts via SignalR.
        /// </summary>
        [HttpPost]
        public async Task<ActionResult<Alert>> CreateAlert([FromBody] CreateAlertDto dto)
        {
            var camera = await _db.Cameras.FindAsync(dto.CameraId);
            if (camera == null)
                return NotFound($"Camera with ID {dto.CameraId} not found.");

            var alert = new Alert
            {
                CameraId = dto.CameraId,
                Type = dto.Type,
                Severity = dto.Severity,
                Timestamp = DateTime.UtcNow,
                CreatedAt = DateTime.UtcNow
            };

            _db.Alerts.Add(alert);
            await _db.SaveChangesAsync();

            // Broadcast alert to all connected SignalR clients
            await _hubContext.Clients.All.SendAsync("ReceiveAlert", new
            {
                alert.Id,
                alert.CameraId,
                CameraName = camera.Name,
                alert.Type,
                alert.Severity,
                alert.Timestamp
            });

            return CreatedAtAction(nameof(GetAlerts), new { id = alert.Id }, alert);
        }
    }

    /// <summary>
    /// DTO for creating alerts from the Python AI service.
    /// </summary>
    public class CreateAlertDto
    {
        public int CameraId { get; set; }
        public string Type { get; set; } = "SAFE_OPEN";
        public string Severity { get; set; } = "Critical";
    }

    /// <summary>
    /// DTO returned by GET /api/alerts — avoids circular reference and oversized payload.
    /// </summary>
    public class AlertDto
    {
        public int Id { get; set; }
        public int CameraId { get; set; }
        public string? CameraName { get; set; }
        public string Type { get; set; } = string.Empty;
        public string Severity { get; set; } = string.Empty;
        public DateTime Timestamp { get; set; }
        public DateTime CreatedAt { get; set; }
    }
}
