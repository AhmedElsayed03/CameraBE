using System.ComponentModel.DataAnnotations;

namespace CameraBE.Entities
{
    public class Camera
    {
        [Key]
        public int Id { get; set; }

        [Required]
        [MaxLength(200)]
        public string Name { get; set; } = string.Empty;

        /// <summary>
        /// Original RTSP stream URL (e.g. rtsp://localhost:8554/mystream)
        /// </summary>
        [Required]
        [MaxLength(500)]
        public string RtspUrl { get; set; } = string.Empty;

        /// <summary>
        /// Processed RTSP stream URL with bounding boxes (e.g. rtsp://localhost:8554/processed)
        /// </summary>
        [Required]
        [MaxLength(500)]
        public string ProcessedRtspUrl { get; set; } = string.Empty;

        public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

        // Navigation property
        public ICollection<Alert> Alerts { get; set; } = new List<Alert>();
    }
}
