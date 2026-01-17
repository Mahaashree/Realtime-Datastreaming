"""
Message Priority Processor for Live Pipeline - Dual-Pipeline System
Assigns message priorities based on detection labels for live streaming
"""
import time
from typing import Dict, Any, Optional
from enum import Enum

class MessagePriority(Enum):
    """Message priority levels."""
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

class MessagePriorityProcessor:
    """Assigns message priorities based on detection labels for live streaming."""
    
    def __init__(self):
        # Priority mapping for detection labels
        self.detection_priority_map = {
            # Critical priority - immediate attention required
            'eyes_closed': MessagePriority.CRITICAL,
            'head_nodding': MessagePriority.CRITICAL,
            
            # High priority - concerning drowsiness indicators
            'drowsy': MessagePriority.HIGH,
            'yawning': MessagePriority.HIGH,
            
            # Normal priority - standard states
            'alert': MessagePriority.NORMAL,
            'distracted': MessagePriority.NORMAL,
            
            # Default for unknown labels
            'unknown': MessagePriority.NORMAL
        }
        
        # Statistics
        self.stats = {
            'messages_processed': 0,
            'priority_counts': {
                'critical': 0,
                'high': 0,
                'normal': 0,
                'low': 0
            },
            'detection_label_counts': {},
            'processing_start_time': time.time()
        }
    
    def get_message_priority(self, detection_label: str) -> MessagePriority:
        """
        Assigns message priority based on detection label.
        
        Args:
            detection_label: The drowsiness detection label
            
        Returns:
            MessagePriority enum value
        """
        # Normalize the detection label
        normalized_label = detection_label.lower().strip() if detection_label else 'unknown'
        
        # Get priority from mapping, default to normal
        priority = self.detection_priority_map.get(normalized_label, MessagePriority.NORMAL)
        
        # Update statistics
        self.stats['messages_processed'] += 1
        self.stats['priority_counts'][priority.value] += 1
        
        if normalized_label in self.stats['detection_label_counts']:
            self.stats['detection_label_counts'][normalized_label] += 1
        else:
            self.stats['detection_label_counts'][normalized_label] = 1
        
        return priority
    
    def process_message_for_live_pipeline(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process message for live pipeline by adding priority information.
        
        Args:
            payload: Raw telemetry message payload
            
        Returns:
            Enhanced payload with priority metadata
        """
        # Extract detection label
        detection_label = payload.get('detection_label', 'unknown')
        
        # Determine message priority
        message_priority = self.get_message_priority(detection_label)
        
        # Create enhanced payload
        enhanced_payload = payload.copy()
        
        # Add live pipeline metadata
        if 'pipeline_metadata' not in enhanced_payload:
            enhanced_payload['pipeline_metadata'] = {}
        
        enhanced_payload['pipeline_metadata'].update({
            'message_priority': message_priority.value,
            'detection_label_normalized': detection_label.lower().strip() if detection_label else 'unknown',
            'priority_assigned_at': time.time(),
            'processor_version': '1.0'
        })
        
        return enhanced_payload
    
    def get_priority_level(self, priority: str) -> int:
        """
        Convert priority string to numeric level for sorting.
        
        Args:
            priority: Priority string (critical, high, normal, low)
            
        Returns:
            Numeric priority level (1=highest, 4=lowest)
        """
        priority_levels = {
            'critical': 1,
            'high': 2,
            'normal': 3,
            'low': 4
        }
        return priority_levels.get(priority.lower(), 3)  # Default to normal (3)
    
    def is_high_priority_message(self, detection_label: str) -> bool:
        """
        Check if message should be treated as high priority.
        
        Args:
            detection_label: The drowsiness detection label
            
        Returns:
            True if message is critical or high priority
        """
        priority = self.get_message_priority(detection_label)
        return priority in [MessagePriority.CRITICAL, MessagePriority.HIGH]
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """Get comprehensive processing statistics."""
        current_time = time.time()
        uptime = current_time - self.stats['processing_start_time']
        
        # Calculate processing rate
        processing_rate = self.stats['messages_processed'] / max(uptime, 1)
        
        # Calculate priority distribution percentages
        total_messages = self.stats['messages_processed']
        priority_percentages = {}
        
        if total_messages > 0:
            for priority, count in self.stats['priority_counts'].items():
                priority_percentages[priority] = (count / total_messages) * 100
        else:
            priority_percentages = {p: 0.0 for p in self.stats['priority_counts'].keys()}
        
        return {
            'messages_processed': total_messages,
            'processing_rate_per_second': round(processing_rate, 2),
            'uptime_seconds': round(uptime, 2),
            'priority_counts': self.stats['priority_counts'].copy(),
            'priority_percentages': priority_percentages,
            'detection_label_counts': self.stats['detection_label_counts'].copy(),
            'most_common_detection': max(self.stats['detection_label_counts'].items(), 
                                       key=lambda x: x[1])[0] if self.stats['detection_label_counts'] else 'none',
            'high_priority_percentage': priority_percentages.get('critical', 0) + priority_percentages.get('high', 0)
        }
    
    def add_custom_priority_mapping(self, detection_label: str, priority: MessagePriority):
        """
        Add or update custom priority mapping for detection labels.
        
        Args:
            detection_label: The detection label to map
            priority: The priority to assign
        """
        normalized_label = detection_label.lower().strip()
        self.detection_priority_map[normalized_label] = priority
        print(f"INFO Added custom priority mapping: {normalized_label} -> {priority.value}")
    
    def remove_custom_priority_mapping(self, detection_label: str):
        """
        Remove custom priority mapping (will fall back to default).
        
        Args:
            detection_label: The detection label to remove
        """
        normalized_label = detection_label.lower().strip()
        if normalized_label in self.detection_priority_map:
            removed_priority = self.detection_priority_map.pop(normalized_label)
            print(f"INFO Removed custom priority mapping: {normalized_label} (was: {removed_priority.value})")
            return True
        return False
    
    def reset_stats(self):
        """Reset processing statistics."""
        self.stats = {
            'messages_processed': 0,
            'priority_counts': {
                'critical': 0,
                'high': 0,
                'normal': 0,
                'low': 0
            },
            'detection_label_counts': {},
            'processing_start_time': time.time()
        }
        print("INFO Message priority processor statistics reset")
    
    def get_priority_mapping(self) -> Dict[str, str]:
        """Get current priority mapping configuration."""
        return {label: priority.value for label, priority in self.detection_priority_map.items()}


# Global message priority processor instance
message_priority_processor = MessagePriorityProcessor()


def get_message_priority(detection_label: str) -> str:
    """
    Convenience function to get message priority as string.
    
    Args:
        detection_label: The drowsiness detection label
        
    Returns:
        Priority string (critical, high, normal, low)
    """
    return message_priority_processor.get_message_priority(detection_label).value


def process_message_for_live_pipeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function to process message for live pipeline.
    
    Args:
        payload: Raw telemetry message payload
        
    Returns:
        Enhanced payload with priority metadata
    """
    return message_priority_processor.process_message_for_live_pipeline(payload)


def is_high_priority_message(detection_label: str) -> bool:
    """
    Convenience function to check if message is high priority.
    
    Args:
        detection_label: The drowsiness detection label
        
    Returns:
        True if message is critical or high priority
    """
    return message_priority_processor.is_high_priority_message(detection_label)