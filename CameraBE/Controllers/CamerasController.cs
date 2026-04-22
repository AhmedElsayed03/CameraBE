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
        /// GET /api/cameras — Returns all cameras with their RTSP URLs.
        /// </summary>
        [HttpGet]
        public async Task<ActionResult<IEnumerable<Camera>>> GetCameras()
        {
            return await _db.Cameras.ToListAsync();
        }
    }
}
