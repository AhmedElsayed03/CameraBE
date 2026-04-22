using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace CameraBE.Entities
{
    public class Alert
    {
        [Key]
        public int Id { get; set; }

        [Required]
        public int CameraId { get; set; }

        /// <summary>
        /// Alert type — e.g. "MULTIPLE_PEOPLE". Extensible for future types like "NO_HELMET".
        /// </summary>
        [Required]
        [MaxLength(100)]
        public string Type { get; set; } = string.Empty;

        /// <summary>
        /// Severity level — e.g. "Warning", "Critical"
        /// </summary>
        [Required]
        [MaxLength(50)]
        public string Severity { get; set; } = "Warning";

        public DateTime Timestamp { get; set; } = DateTime.UtcNow;

        public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

        // Navigation property
        [ForeignKey(nameof(CameraId))]
        public Camera? Camera { get; set; }
    }
}
