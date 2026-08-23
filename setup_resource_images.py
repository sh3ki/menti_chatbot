#!/usr/bin/env python3
"""
Download images for offline resources and organize them.
Uses free image APIs (Unsplash) to fetch relevant images.
"""

import os
import requests
from pathlib import Path

# Resource image URLs from Unsplash (free to use, no API key required for basic usage)
RESOURCE_IMAGES = {
    'article-001': {
        'title': 'Understanding Anxiety Disorders',
        'url': 'https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'anxiety-guide.jpg'
    },
    'article-002': {
        'title': 'Depression: Recognition and Recovery',
        'url': 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'depression-guide.jpg'
    },
    'guide-001': {
        'title': 'Sleep Hygiene',
        'url': 'https://images.unsplash.com/photo-1534438327276-14e5300c3a48?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'sleep-hygiene.jpg'
    },
    'guide-002': {
        'title': 'Exercise and Mental Health',
        'url': 'https://images.unsplash.com/photo-1571902943202-507ec2618e8f?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'exercise-mental-health.jpg'
    },
    'guide-003': {
        'title': 'Nutrition for Mental Health',
        'url': 'https://images.unsplash.com/photo-1512621776951-a57141f2eefd?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'nutrition-health.jpg'
    },
    'technique-001': {
        'title': 'Cognitive Behavioral Therapy Basics',
        'url': 'https://images.unsplash.com/photo-1484480974693-6ca0a78fb36b?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'cbt-basics.jpg'
    },
    'technique-002': {
        'title': 'Mindfulness and Meditation',
        'url': 'https://images.unsplash.com/photo-1506126613408-eca07ce68773?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'mindfulness-meditation.jpg'
    },
    'awareness-001': {
        'title': 'Breaking Mental Health Stigma',
        'url': 'https://images.unsplash.com/photo-1552664730-d307ca884978?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'mental-health-awareness.jpg'
    },
    'awareness-002': {
        'title': 'Understanding the Stress-Health Connection',
        'url': 'https://images.unsplash.com/photo-1506157786151-b8491531f063?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'stress-health.jpg'
    },
    'awareness-003': {
        'title': 'Building Emotional Resilience',
        'url': 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'emotional-resilience.jpg'
    },
    'resource-001': {
        'title': 'Emotional Regulation Workbook',
        'url': 'https://images.unsplash.com/photo-1509042239860-f550ce710b93?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'emotional-regulation.jpg'
    },
    'breathing-001': {
        'title': '2-Minute Calming Breath',
        'url': 'https://images.unsplash.com/photo-1583454110551-21f2fa2afe61?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'calming-breath.jpg'
    },
    'grounding-001': {
        'title': '5-4-3-2-1 Grounding',
        'url': 'https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'grounding-senses.jpg'
    },
    'stress-001': {
        'title': 'Stress Response Reset',
        'url': 'https://images.unsplash.com/photo-1519389950473-47ba0277781c?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'stress-reset.jpg'
    },
    'selfcare-001': {
        'title': 'Low-Energy Self-Care Plan',
        'url': 'https://images.unsplash.com/photo-1493857671505-72967e2e2760?ixlib=rb-4.0.3&auto=format&fit=crop&w=600&q=80',
        'filename': 'self-care.jpg'
    },
    'hotline-001': {
        'title': 'Crisis Contacts',
        'url': 'https://images.unsplash.com/photo-1576091160550-2173dba999ef?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=600&q=80',
        'filename': 'crisis-support.jpg'
    },
}


def download_images():
    """Download all resource images."""
    resources_dir = Path(__file__).parent / 'assets' / 'resources'
    resources_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📁 Created resources directory: {resources_dir}")
    print(f"📥 Downloading {len(RESOURCE_IMAGES)} resource images...\n")
    
    downloaded = 0
    failed = []
    
    for resource_id, info in RESOURCE_IMAGES.items():
        filepath = resources_dir / info['filename']
        
        try:
            print(f"  ⏳ Downloading {info['title']}...", end=' ')
            response = requests.get(info['url'], timeout=10)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            print(f"✅ ({info['filename']})")
            downloaded += 1
            
        except Exception as e:
            print(f"❌ Failed")
            failed.append((resource_id, info['title'], str(e)))
    
    print(f"\n✨ Download Summary:")
    print(f"  ✅ Successfully downloaded: {downloaded}/{len(RESOURCE_IMAGES)}")
    
    if failed:
        print(f"  ❌ Failed: {len(failed)}")
        for res_id, title, error in failed:
            print(f"     - {title}: {error}")
    
    return downloaded, failed


def print_resource_mapping():
    """Print the resource-to-image mapping for reference."""
    print("\n" + "="*70)
    print("Resource Image Mapping (for app.py)")
    print("="*70 + "\n")
    
    for resource_id, info in RESOURCE_IMAGES.items():
        image_path = f"/static/resources/{info['filename']}"
        print(f"'{resource_id}': '{image_path}',")
    
    print("\n" + "="*70)


if __name__ == '__main__':
    print("\n🎨 Menti Offline Resources - Image Setup Tool\n")
    
    try:
        downloaded, failed = download_images()
        print_resource_mapping()
        
        if not failed:
            print("✨ All images downloaded successfully!")
            print("📝 Next step: Update app.py to add image paths to resources")
        else:
            print(f"⚠️  {len(failed)} images failed to download. Manual review needed.")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
