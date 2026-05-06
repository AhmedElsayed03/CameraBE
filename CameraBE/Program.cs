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
                options.UseNpgsql(builder.Configuration.GetConnectionString("DefaultConnection")));

            // --- Controllers with JSON cycle handling ---
            builder.Services.AddControllers()
                .AddJsonOptions(o => o.JsonSerializerOptions.ReferenceHandler = ReferenceHandler.IgnoreCycles);

            // --- SignalR for real-time alert push ---
            builder.Services.AddSignalR();

            // --- CORS: allow frontend origin with credentials (required for SignalR) ---
            builder.Services.AddCors(options =>
            {
                options.AddPolicy("AllowAll", policy =>
                    policy.WithOrigins("http://localhost:3000", "http://localhost:5173") // add your frontend URL(s) here
                          .AllowAnyHeader()
                          .AllowAnyMethod()
                          .AllowCredentials()); // required for SignalR
            });

            builder.Services.AddEndpointsApiExplorer();
            builder.Services.AddSwaggerGen();

            var app = builder.Build();

            // --- Tables already exist in the database, no migrations needed ---

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
