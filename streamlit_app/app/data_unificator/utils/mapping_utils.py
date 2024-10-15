# utils/mapping_utils.py

import pandas as pd
from difflib import SequenceMatcher
import yaml
import os
from data_unificator.utils.logging_utils import log_error

def extract_fields_metadata(data):
    """
    Extract metadata for each field in the imported data.
    Handles both tabular (DataFrame) and non-tabular (JSON, XML).
    """
    metadata = {}
    max_sample_values = 5  # Limit the number of sample values per field
    if isinstance(data, pd.DataFrame):
        for column in data.columns:
            metadata[column] = {
                'dtype': str(data[column].dtype),
                'sample_values': data[column].dropna().unique().tolist()[:max_sample_values],
            }
    else:
        # Handle non-tabular data structures
        def traverse(data, path=""):
            if isinstance(data, dict):
                for key, value in data.items():
                    current_path = f"{path}.{key}" if path else key
                    if isinstance(value, (dict, list)):
                        traverse(value, current_path)
                    else:
                        dtype = type(value).__name__
                        if current_path not in metadata:
                            metadata[current_path] = {
                                'dtype': dtype,
                                'sample_values': [value],
                            }
                        else:
                            if len(metadata[current_path]['sample_values']) < max_sample_values:
                                metadata[current_path]['sample_values'].append(value)
            elif isinstance(data, list):
                for item in data:
                    traverse(item, path)
            else:
                pass  # Leaf node

        traverse(data)
    return metadata

def identify_overlaps(field_metadata):
    """
    Identify overlapping fields based on shared field names across sources.
    For each shared field name, verify whether the data types and data value patterns are similar.
    """
    overlaps = []
    field_info = {}
    # Collect field info for each field name across sources
    for source, meta in field_metadata.items():
        fields = meta['metadata']
        for field_name, field_meta in fields.items():
            if field_name not in field_info:
                field_info[field_name] = {}
            field_info[field_name][source] = field_meta

    # Now, for each field name that exists in more than one source
    for field_name, sources in field_info.items():
        if len(sources) > 1:
            # Collect data types and sample values from all sources
            data_types = {}
            value_patterns = {}
            for source_name, field_meta in sources.items():
                dtype = field_meta['dtype']
                sample_values = field_meta['sample_values']
                data_types[source_name] = dtype
                # Create a pattern representation of sample values
                pattern = [type(value).__name__ for value in sample_values]
                value_patterns[source_name] = pattern
            overlaps.append({
                'field_name': field_name,
                'sources': list(sources.keys()),
                'data_types': data_types,
                'value_patterns': value_patterns
            })
    return overlaps

def rename_fields_in_data_structure(data, field_name_mapping):
    """
    Rename fields in data structure (dict/list) based on field name mapping.
    """
    if isinstance(data, dict):
        new_data = {}
        for key, value in data.items():
            new_key = field_name_mapping.get(key, key)
            new_data[new_key] = rename_fields_in_data_structure(value, field_name_mapping)
        return new_data
    elif isinstance(data, list):
        return [rename_fields_in_data_structure(item, field_name_mapping) for item in data]
    else:
        return data

def convert_field_types_in_data_structure(data, field_type_mapping, path=""):
    """
    Convert field types in a data structure based on field type mapping.
    """
    if isinstance(data, dict):
        new_data = {}
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if current_path in field_type_mapping:
                new_type = field_type_mapping[current_path]
                new_value = convert_value_to_type(value, new_type)
                new_data[key] = new_value
            else:
                new_data[key] = convert_field_types_in_data_structure(value, field_type_mapping, current_path)
        return new_data
    elif isinstance(data, list):
        return [convert_field_types_in_data_structure(item, field_type_mapping, path) for item in data]
    else:
        return data

def convert_value_to_type(value, new_type):
    try:
        if new_type == 'int':
            return int(value)
        elif new_type == 'float':
            return float(value)
        elif new_type == 'str':
            return str(value)
        elif new_type == 'datetime':
            from datetime import datetime
            return datetime.strptime(value, "%Y-%m-%d")
        else:
            return value
    except Exception as e:
        log_error(f"Data type convert - {value} to {new_type} - {str(e)}")
        return value  # Return original value if conversion fails

def detect_conflicts(aligned_data):
    """
    Detect conflicts in data, formats, types, key pairs, and data structure.
    """
    conflicts = {}
    data_frames = []
    source_names = []
    for data_dict in aligned_data:
        df = data_dict['data']
        file_name = data_dict['file']
        if isinstance(df, pd.DataFrame):
            df_copy = df.copy()
            df_copy['_source'] = file_name
            data_frames.append(df_copy)
            source_names.append(file_name)
        else:
            continue  # Skip non-DataFrame data

    # Find common columns
    if not data_frames:
        return conflicts
    common_columns = set.intersection(*(set(df.columns) for df in data_frames))
    common_columns.discard('_source')
    key_columns = list(common_columns)

    if not key_columns:
        return conflicts

    # Concatenate all data
    combined_df = pd.concat(data_frames, ignore_index=True, sort=False)
    grouped = combined_df.groupby(key_columns)

    # Detect conflicts in groups
    for group_keys, group in grouped:
        if len(group['_source'].unique()) > 1:
            conflicting_fields = {}
            for column in combined_df.columns:
                if column in key_columns or column == '_source':
                    continue
                values = group[column].dropna().unique()
                if len(values) > 1:
                    conflicting_values = {}
                    for source in source_names:
                        source_values = group[group['_source'] == source][column].dropna().unique()
                        if len(source_values) > 0:
                            conflicting_values[source] = source_values.tolist()
                    conflicting_fields[column] = conflicting_values
            if conflicting_fields:
                conflicts[group_keys] = conflicting_fields
    return conflicts

def resolve_conflicts(aligned_data, conflicts, strategy, source_weights, source_hierarchy):
    """
    Resolve conflicts based on selected strategy.
    """
    if strategy == "Manual":
        return aligned_data  # No implementation for manual resolution
    elif strategy == 'Hierarchy-based':
        resolved_data = resolve_conflicts_hierarchy(aligned_data, source_hierarchy)
    elif strategy == 'Weight-based':
        resolved_data = resolve_conflicts_weighted(aligned_data, source_weights)
    elif strategy == 'Time-based':
        resolved_data = resolve_conflicts_time_based(aligned_data)
    else:
        resolved_data = aligned_data
    return resolved_data

def resolve_conflicts_hierarchy(aligned_data, source_hierarchy):
    """
    Resolve conflicts based on source hierarchy.
    The source higher in the hierarchy has precedence.
    """
    source_priority = {source: idx for idx, source in enumerate(source_hierarchy)}
    data_frames = []
    for data_dict in aligned_data:
        df = data_dict['data']
        file_name = data_dict['file']
        if isinstance(df, pd.DataFrame):
            df_copy = df.copy()
            df_copy['_source'] = file_name
            df_copy['_priority'] = source_priority.get(file_name, len(source_priority))
            data_frames.append(df_copy)
        else:
            continue

    if not data_frames:
        return aligned_data

    combined_df = pd.concat(data_frames, ignore_index=True, sort=False)
    common_columns = set.intersection(*(set(df.columns) for df in data_frames))
    common_columns.discard('_source')
    common_columns.discard('_priority')
    key_columns = list(common_columns)

    if not key_columns:
        return aligned_data

    combined_df = combined_df.sort_values('_priority')
    resolved_df = combined_df.drop_duplicates(subset=key_columns, keep='first')
    resolved_df = resolved_df.drop(columns=['_source', '_priority'])
    resolved_data = [{'file': 'resolved_data', 'data': resolved_df}]
    return resolved_data

def resolve_conflicts_weighted(aligned_data, source_weights):
    """
    Resolve conflicts based on source weights.
    The source with higher weight has precedence.
    """
    data_frames = []
    for data_dict in aligned_data:
        df = data_dict['data']
        file_name = data_dict['file']
        weight = source_weights.get(file_name, 0)
        if isinstance(df, pd.DataFrame):
            df_copy = df.copy()
            df_copy['_source'] = file_name
            df_copy['_weight'] = weight
            data_frames.append(df_copy)
        else:
            continue

    if not data_frames:
        return aligned_data

    combined_df = pd.concat(data_frames, ignore_index=True, sort=False)
    common_columns = set.intersection(*(set(df.columns) for df in data_frames))
    common_columns.discard('_source')
    common_columns.discard('_weight')
    key_columns = list(common_columns)

    if not key_columns:
        return aligned_data

    combined_df = combined_df.sort_values('_weight', ascending=False)
    resolved_df = combined_df.drop_duplicates(subset=key_columns, keep='first')
    resolved_df = resolved_df.drop(columns=['_source', '_weight'])
    resolved_data = [{'file': 'resolved_data', 'data': resolved_df}]
    return resolved_data

def resolve_conflicts_time_based(aligned_data):
    """
    Resolve conflicts based on the latest timestamp.
    Assumes there is a 'timestamp' field in the data.
    """
    data_frames = []
    for data_dict in aligned_data:
        df = data_dict['data']
        file_name = data_dict['file']
        if isinstance(df, pd.DataFrame) and 'timestamp' in df.columns:
            df_copy = df.copy()
            df_copy['_source'] = file_name
            data_frames.append(df_copy)
        else:
            continue

    if not data_frames:
        return aligned_data

    combined_df = pd.concat(data_frames, ignore_index=True, sort=False)
    combined_df['timestamp'] = pd.to_datetime(combined_df['timestamp'], errors='coerce')
    combined_df = combined_df.dropna(subset=['timestamp'])

    common_columns = set.intersection(*(set(df.columns) for df in data_frames))
    common_columns.discard('_source')
    common_columns.discard('timestamp')
    key_columns = list(common_columns)

    if not key_columns:
        return aligned_data

    combined_df = combined_df.sort_values('timestamp', ascending=False)
    resolved_df = combined_df.drop_duplicates(subset=key_columns, keep='first')
    resolved_df = resolved_df.drop(columns=['_source', 'timestamp'])
    resolved_data = [{'file': 'resolved_data', 'data': resolved_df}]
    return resolved_data

def verify_data_types(resolved_data):
    """
    Verify that fields mapped together have compatible data types.
    """
    incompatibilities = {}
    field_types = {}
    for data in resolved_data:
        df = data['data']
        if isinstance(df, pd.DataFrame):
            for column in df.columns:
                dtype = str(df[column].dtype)
                if column not in field_types:
                    field_types[column] = set()
                field_types[column].add(dtype)
        else:
            collect_field_types_in_data_structure(df, field_types)

    for field, types in field_types.items():
        if len(types) > 1:
            incompatibilities[field] = list(types)
    return incompatibilities

def collect_field_types_in_data_structure(data, field_types, path=""):
    """
    Collect data types from data structures to check for incompatibilities.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if isinstance(value, (dict, list)):
                collect_field_types_in_data_structure(value, field_types, current_path)
            else:
                dtype = type(value).__name__
                if current_path not in field_types:
                    field_types[current_path] = set()
                field_types[current_path].add(dtype)
    elif isinstance(data, list):
        for item in data:
            collect_field_types_in_data_structure(item, field_types, path)

def convert_data_types(resolved_data, user_conversions):
    """
    Convert data types of selected fields as per user input.
    """
    for data in resolved_data:
        df = data['data']
        if isinstance(df, pd.DataFrame):
            for field, new_type in user_conversions.items():
                try:
                    df[field] = df[field].astype(new_type)
                except Exception as e:
                    log_error(f"Error converting field '{field}' to '{new_type}': {str(e)}")
        else:
            df = convert_field_types_in_data_structure(df, user_conversions)
            data['data'] = df  # Update the data in the resolved_data
    return resolved_data

def save_mapping_dictionary(mapping_dictionary, version):
    """
    Save mapping dictionary to a YAML file with versioning.
    """
    file_name = f"mapping_dictionary_v{version}.yaml"
    os.makedirs('mappings', exist_ok=True)
    with open(os.path.join('mappings', file_name), 'w') as f:
        yaml.dump(mapping_dictionary, f)

def load_mapping_dictionary():
    """
    Load the latest mapping dictionary.
    """
    mapping_files = [f for f in os.listdir('mappings') if f.startswith('mapping_dictionary_v') and f.endswith('.yaml')]
    if not mapping_files:
        raise FileNotFoundError("No mapping dictionary found.")
    mapping_files.sort(key=lambda x: int(x[len('mapping_dictionary_v'):-len('.yaml')]))
    latest_file = mapping_files[-1]
    with open(os.path.join('mappings', latest_file), 'r') as f:
        mapping_dictionary = yaml.safe_load(f)
    return mapping_dictionary

def version_mapping_dictionary():
    """
    Get the next version number for the mapping dictionary.
    """
    mapping_files = [f for f in os.listdir('mappings') if f.startswith('mapping_dictionary_v') and f.endswith('.yaml')]
    if not mapping_files:
        return 1
    mapping_files.sort(key=lambda x: int(x[len('mapping_dictionary_v'):-len('.yaml')]))
    latest_file = mapping_files[-1]
    version_str = latest_file[len('mapping_dictionary_v'):-len('.yaml')]
    version = int(version_str)
    return version + 1
