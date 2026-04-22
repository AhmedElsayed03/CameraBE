using CameraBE.Entities;
using Microsoft.EntityFrameworkCore;

namespace CameraBE.Data
{
    public class AppDbContext : DbContext
    {
        public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

        public DbSet<Camera> Cameras => Set<Camera>();
        public DbSet<Alert> Alerts => Set<Alert>();

        protected override void OnModelCreating(ModelBuilder modelBuilder)
        {
            base.OnModelCreating(modelBuilder);

            // Configure Camera → Alert relationship
            modelBuilder.Entity<Alert>()
                .HasOne(a => a.Camera)
                .WithMany(c => c.Alerts)
                .HasForeignKey(a => a.CameraId)
                .OnDelete(DeleteBehavior.Cascade);

            // Seed a default camera
            modelBuilder.Entity<Camera>().HasData(new Camera
            {
                Id = 1,
                Name = "Main Entrance",
                RtspUrl = "rtsp://localhost:8554/mystream",
                ProcessedRtspUrl = "rtsp://localhost:8554/processed",
                CreatedAt = new DateTime(2025, 1, 1, 0, 0, 0, DateTimeKind.Utc)
            });
        }
    }
}
