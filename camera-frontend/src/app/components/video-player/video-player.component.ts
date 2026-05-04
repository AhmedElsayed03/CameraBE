import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

/**
 * Displays the processed RTSP stream via WebRTC using MediaMTX's built-in WHEP endpoint.
 *
 * MediaMTX exposes a WebRTC player at http://localhost:8889/<streamname>
 * We embed it in an iframe, or use the WHEP API for a native video element.
 *
 * Approach: Use an iframe pointing to MediaMTX's built-in web player for simplicity.
 * For production, you can use the WHEP API directly with RTCPeerConnection.
 */
@Component({
  selector: 'app-video-player',
  standalone: true,
  template: `
    <div class="video-container">
      <h2>Live Processed Feed</h2>
      <iframe
        #videoFrame
        [src]="streamUrl"
        width="800"
        height="480"
        frameBorder="0"
        allowfullscreen>
      </iframe>
    </div>
  `,
  styles: [`
    .video-container {
      text-align: center;
      margin: 20px 0;
    }
    h2 {
      color: #333;
      margin-bottom: 10px;
    }
    iframe {
      border: 2px solid #444;
      border-radius: 8px;
      background: #000;
    }
  `]
})
export class VideoPlayerComponent implements OnInit {
  streamUrl!: SafeResourceUrl;

  @ViewChild('videoFrame') videoFrame!: ElementRef;

  constructor(private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    // MediaMTX built-in WebRTC player page
    this.streamUrl = this.sanitizer.bypassSecurityTrustResourceUrl(
      'http://localhost:8889/processed/'
    );
  }
}
