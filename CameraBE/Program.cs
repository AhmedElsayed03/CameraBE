
using CameraBE.Data;
using CameraBE.Hubs;
using Microsoft.EntityFrameworkCore;
using System.Text.Json.Serialization;

namespace CameraBE
{
    public class Program
    {
        public static void Main(string[] args)
        {
            var builder = WebApplication.CreateBuilder(args);

            // --- Database ---
            builder.Services.AddDbContext<AppDbContext>(options =>
                options.UseSqlServer(builder.Configuration.GetConnectionString("DefaultConnection")));

            // --- Controllers with JSON cycle handling ---
            builder.Services.AddControllers()
                .AddJsonOptions(o => o.JsonSerializerOptions.ReferenceHandler = ReferenceHandler.IgnoreCycles);

            // --- SignalR for real-time alert push ---
            builder.Services.AddSignalR();

            // --- CORS: allow any origin for frontend/API testing ---
            builder.Services.AddCors(options =>
            {
                options.AddPolicy("AllowAll", policy =>
                    policy.AllowAnyOrigin()
                          .AllowAnyHeader()
                          .AllowAnyMethod());
            });

            builder.Services.AddEndpointsApiExplorer();
            builder.Services.AddSwaggerGen();

            var app = builder.Build();

            // --- Auto-migrate and seed on startup ---
            using (var scope = app.Services.CreateScope())
            {
                var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
                db.Database.Migrate();
            }

            if (app.Environment.IsDevelopment())
            {
                app.UseSwagger();
                app.UseSwaggerUI();
            }

            app.UseCors("AllowAll");

            app.UseAuthorization();

            app.MapControllers();

            // Map SignalR hub endpoint
            app.MapHub<AlertHub>("/alertHub");

            app.Run();
        }
    }
}
