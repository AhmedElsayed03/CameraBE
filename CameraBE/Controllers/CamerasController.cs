using CameraBE.Data;
using CameraBE.Entities;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace CameraBE.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class CamerasController : ControllerBase
    {
        private readonly AppDbContext _db;

        public CamerasController(AppDbContext db)
        {
            _db = db;
        }

        /// <summary>
        /// GET /api/cameras — Returns all cameras without alerts.
        /// </summary>
        [HttpGet]
        public async Task<ActionResult<IEnumerable<Camera>>> GetCameras()
        {
            return await _db.Cameras.ToListAsync();
        }

        /// <summary>
        /// GET /api/cameras/with-alerts — Returns all cameras including their alerts.
        /// </summary>
        [HttpGet("with-alerts")]
        public async Task<ActionResult<IEnumerable<CameraDto>>> GetCamerasWithAlerts()
        {
            return await _db.Cameras
                .Include(c => c.Alerts)
                .Select(c => new CameraDto
                {
                    Id = c.Id,
                    Name = c.Name,
                    RtspUrl = c.RtspUrl,
                    ProcessedRtspUrl = c.ProcessedRtspUrl,
                    CreatedAt = c.CreatedAt,
                    Alerts = c.Alerts.Select(a => new CameraAlertDto
                    {
                        Id = a.Id,
                        Type = a.Type,
                        Severity = a.Severity,
                        Timestamp = a.Timestamp,
                        CreatedAt = a.CreatedAt
                    }).ToList()
                })
                .ToListAsync();
        }
    }

    public class CameraDto
    {
        public int Id { get; set; }
        public string Name { get; set; } = string.Empty;
        public string RtspUrl { get; set; } = string.Empty;
        public string ProcessedRtspUrl { get; set; } = string.Empty;
        public DateTime CreatedAt { get; set; }
        public List<CameraAlertDto> Alerts { get; set; } = new();
    }

    public class CameraAlertDto
    {
        public int Id { get; set; }
        public string Type { get; set; } = string.Empty;
        public string Severity { get; set; } = string.Empty;
        public DateTime Timestamp { get; set; }
        public DateTime CreatedAt { get; set; }
    }
}
