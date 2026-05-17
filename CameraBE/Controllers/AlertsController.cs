using CameraBE.Data;
using CameraBE.Entities;
using CameraBE.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace CameraBE.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class AlertsController : ControllerBase
    {
        private readonly AppDbContext _db;
        private readonly AlertDelayService _alertDelayService;

        public AlertsController(AppDbContext db, AlertDelayService alertDelayService)
        {
            _db = db;
            _alertDelayService = alertDelayService;
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

            // Hand off to the delay service — it will broadcast via SignalR only after
            // the alert has been sustained continuously for SignalRDelaySeconds.
            _alertDelayService.RegisterAlert(dto.CameraId, dto.Type, alert.Id, camera.Name, dto.Severity);

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
