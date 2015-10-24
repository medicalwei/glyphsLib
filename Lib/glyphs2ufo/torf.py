# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


__all__ = [
    'to_robofab', 'clear_data', 'set_redundant_data', 'build_family_name',
    'build_style_name', 'build_postscript_name'
]


import re
from robofab.world import RFont


GLYPHS_PREFIX = 'com.schriftgestaltung.'
ROBOFONT_PREFIX = 'com.typemytype.robofont.'


def to_robofab(data, italic=False, include_instances=False, debug=False):
    """Take .glyphs file data and load it into RFonts.

    Takes in data as a dictionary structured according to
    https://github.com/schriftgestalt/GlyphsSDK/blob/master/GlyphsFileFormat.md
    and returns a list of RFonts, one per master.

    If debug is True, returns unused input data instead of the resulting RFonts.
    """

    feature_prefixes, classes, features = [], [], []
    for f in data.get('featurePrefixes', []):
        feature_prefixes.append((f.pop('name'), f.pop('code')))
    for c in data.get('classes', []):
        classes.append((c.pop('name'), c.pop('code'), c.pop('automatic', None)))
    for f in data.get('features', []):
        features.append((f.pop('name'), f.pop('code'), f.pop('automatic', None),
                         f.pop('disabled', None), f.pop('notes', None)))
    kerning_groups = {}

    #TODO(jamesgk) maybe create one font at a time to reduce memory usage
    rfonts, master_id_order = generate_base_fonts(data, italic)

    for glyph in data['glyphs']:
        add_glyph_to_groups(kerning_groups, glyph)

        glyph_name = glyph.pop('glyphname')
        if not re.match('^[\w\d._]{1,31}$', glyph_name):
            print (
                'Illegal glyph name "%s". If this is used in the font\'s '
                'feature syntax, it could cause errors.' % glyph_name)

        # pop glyph metadata only once, i.e. not when looping through layers
        metadata_keys = ['unicode', 'lastChange', 'leftMetricsKey',
                         'rightMetricsKey', 'widthMetricsKey']
        glyph_data = {k: glyph.pop(k) for k in metadata_keys if k in glyph}

        for layer in glyph['layers']:
            # whichever attribute we use for layer_id, make sure they are both
            # popped from the layer data
            layer_id = layer.pop('layerId')
            layer_id = layer.pop('associatedMasterId', layer_id)
            rfont = rfonts[layer_id]

            # ensure consistency between layer ids / names
            style_name = layer.pop('name')
            font_style = rfont_style_to_layer_style(rfont)
            if font_style != style_name:
                print (
                    'Inconsistent layer id/name pair: glyph "%s" layer "%s"' %
                    (glyph_name, style_name))
                continue

            rglyph = rfont.newGlyph(glyph_name)
            load_glyph(rglyph, layer, glyph_data)

    for master_id, kerning in data.pop('kerning', {}).iteritems():
        load_kerning(rfonts[master_id].kerning, kerning)

    result = []
    for master_id in master_id_order:
        rfont = rfonts[master_id]
        add_features_to_rfont(rfont, feature_prefixes, classes, features)
        add_groups_to_rfont(rfont, kerning_groups)
        result.append(rfont)

    instances = data.pop('instances')
    if debug:
        return clear_data(data)
    elif include_instances:
        return result, instances
    return result


def clear_data(data):
    """Clear empty list or dict attributes in data.

    This is used to determine what input data provided to to_robofab was not
    loaded into an RFont."""

    data_type = type(data)
    if data_type is dict:
        for key, val in data.items():
            if not clear_data(val):
                del data[key]
        return data
    elif data_type is list:
        i = 0
        while i < len(data):
            val = data[i]
            if not clear_data(val):
                del data[i]
            else:
                i += 1
        return data
    return True


def generate_base_fonts(data, italic):
    """Generate a list of RFonts with metadata loaded from .glyphs data."""

    copyright = data.pop('copyright')
    date_created = to_rf_time(data.pop('date'))
    designer = data.pop('designer')
    designer_url = data.pop('designerURL')
    family_name = data.pop('familyName')
    manufacturer = data.pop('manufacturer')
    manufacturer_url = data.pop('manufacturerURL')
    units_per_em = data.pop('unitsPerEm')
    version_major = data.pop('versionMajor')
    version_minor = data.pop('versionMinor')
    user_data = data.pop('userData', {})

    misc = ['DisplayStrings', 'disablesAutomaticAlignment', 'disablesNiceNames']
    custom_params = parse_custom_params(data, misc)

    rfonts = {}
    master_id_order = []
    for master in data['fontMaster']:
        rfont = RFont()

        rfont.info.familyName = build_family_name(family_name, master, 'width')
        rfont.info.styleName = build_style_name(master, 'weight', italic)

        rfont.info.copyright = copyright
        rfont.info.openTypeNameDesigner = designer
        rfont.info.openTypeNameDesignerURL = designer_url
        rfont.info.openTypeNameManufacturer = manufacturer
        rfont.info.openTypeNameManufacturerURL = manufacturer_url
        rfont.info.openTypeHeadCreated = date_created
        rfont.info.unitsPerEm = units_per_em
        rfont.info.versionMajor = version_major
        rfont.info.versionMinor = version_minor

        rfont.info.ascender = master.pop('ascender')
        rfont.info.capHeight = master.pop('capHeight')
        rfont.info.descender = master.pop('descender')
        rfont.info.postscriptStemSnapH = master.pop('horizontalStems')
        rfont.info.postscriptStemSnapV = master.pop('verticalStems')
        rfont.info.xHeight = master.pop('xHeight')

        set_redundant_data(rfont)
        set_blue_values(rfont, master.pop('alignmentZones', []))
        set_family_user_data(rfont, user_data)
        set_master_user_data(rfont, master.pop('userData', {}))
        set_robofont_guidelines(rfont, master, is_global=True)

        misc = ['weightValue', 'widthValue']
        for name, value in custom_params + parse_custom_params(master, misc):
            if (hasattr(rfont.info, name) and
                name not in rfont.info._deprecatedAttributes):
                setattr(rfont.info, name, value)
            elif name == 'glyphOrder':
                rfont.lib['public.glyphOrder'] = value
            elif name == 'disablesNiceNames':
                # weird thing that Glyphs seems to do, so do it here too
                rfont.lib[GLYPHS_PREFIX + 'useNiceNames'] = int(not value)
            else:
                rfont.lib[GLYPHS_PREFIX + name] = value

        master_id = master.pop('id')
        rfont.lib[GLYPHS_PREFIX + 'fontMasterID'] = master_id
        master_id_order.append(master_id)
        rfonts[master_id] = rfont

    return rfonts, master_id_order


def set_redundant_data(rfont):
    """Set redundant metadata in an RFont, e.g. data based on other data."""

    family_name, style_name = rfont.info.familyName, rfont.info.styleName
    weight = style_name.replace('Italic', '').strip()
    width = family_name.split()[-1]

    rfont.info.openTypeOS2WeightClass = get_weight_code(weight)
    rfont.info.openTypeOS2WidthClass = get_width_code(width)
    if weight and weight != 'Regular':
        rfont.lib[GLYPHS_PREFIX + 'weight'] = weight
    if 'Condensed' in width:
        rfont.lib[GLYPHS_PREFIX + 'width'] = width

    ps_name = build_postscript_name(family_name, style_name)
    rfont.info.postscriptFontName = ps_name
    rfont.info.postscriptFullName = ps_name

    version_str = '%s.%s' % (rfont.info.versionMajor, rfont.info.versionMinor)
    rfont.info.openTypeNameVersion = 'Version ' + version_str
    rfont.info.openTypeNameUniqueID = '%s;%s' % (version_str, ps_name)

    if style_name.lower() in ['regular', 'bold', 'italic', 'bold italic']:
        rfont.info.styleMapStyleName = style_name.lower()
        rfont.info.styleMapFamilyName = family_name
    else:
        rfont.info.styleMapStyleName = 'regular'
        rfont.info.styleMapFamilyName = '%s %s' % (family_name, weight)
    rfont.info.openTypeNamePreferredFamilyName = family_name
    rfont.info.openTypeNamePreferredSubfamilyName = style_name


def set_blue_values(rfont, alignment_zones):
    """Set postscript blue values from Glyphs alignment zones."""

    blue_values = []
    other_blues = []

    for base, offset in sorted(alignment_zones):
        pair = [base, base + offset]
        val_list = blue_values if base >= 0 else other_blues
        val_list.extend(sorted(pair))

    rfont.info.postscriptBlueValues = blue_values
    rfont.info.postscriptOtherBlues = other_blues


def set_robofont_guidelines(rf_obj, glyphs_data, is_global=False):
    """Set guidelines as Glyphs does."""

    guidelines = glyphs_data.get('guideLines')
    if not guidelines:
        return

    new_guidelines = []
    for guideline in guidelines:
        x, y = guideline.pop('position')
        angle = guideline.pop('angle', 0)
        new_guideline = {'x': x, 'y': y, 'angle': angle, 'isGlobal': is_global}

        locked = guideline.pop('locked', False)
        if locked:
            new_guideline['locked'] = True

        new_guidelines.append(new_guideline)
    rf_obj.lib[ROBOFONT_PREFIX + 'guides'] = new_guidelines


def set_robofont_glyph_background(rglyph, glyphs_data):
    """Set glyph background as Glyphs does."""

    background = glyphs_data.get('background')
    if not background:
        return

    new_background = {}
    new_background['lib'] = background.pop('lib', {})

    anchors = []
    for anchor in background.get('anchors', []):
        x, y = anchor.pop('position')
        anchors.append({'x': x, 'y': y, 'name': anchor.pop('name')})
    new_background['anchors'] = anchors

    components = []
    for component in background.get('components', []):
        new_component = {
            'baseGlyph': component.pop('name'),
            'transformation': component.pop('transform', (1, 0, 0, 1, 0, 0))}

        for meta_attr in ['disableAlignment', 'locked']:
            value = component.pop(meta_attr, False)
            if value:
                new_component[meta_attr] = True

        components.append(new_component)
    new_background['components'] = components

    contours = []
    for path in background.get('paths', []):
        points = []
        for x, y, node_type, smooth in path.pop('nodes', []):
            point = {'x': x, 'y': y, 'smooth': smooth}
            if node_type in ['line', 'curve']:
                point['segmentType'] = node_type
            points.append(point)
        contours.append({'points': points, 'closed': path.pop('closed')})
    new_background['contours'] = contours

    new_background['name'] = rglyph.name
    new_background['width'] = rglyph.width
    new_background['unicodes'] = []

    rglyph.lib[ROBOFONT_PREFIX + 'layerData'] = {'background': new_background}


def set_family_user_data(rfont, user_data):
    """Set family-wide user data as Glyphs does."""

    for key, val in user_data.iteritems():
        rfont.lib[key] = val


def set_master_user_data(rfont, user_data):
    """Set master-specific user data as Glyphs does."""

    if not user_data:
        return
    for attr in ['GSOffsetHorizontal', 'GSOffsetVertical']:
        if attr in user_data:
            user_data[attr] = int(user_data[attr])
    rfont.lib[GLYPHS_PREFIX + 'fontMaster.userData'] = user_data


def build_family_name(base_family, data, width_key):
    """Build family name from base name and width string in data."""
    return ('%s %s' % (base_family, data.pop(width_key, ''))).strip()


def build_style_name(data, weight_key, italic):
    """Build style name from weight string in data and whether it's italic."""

    style_name = data.pop(weight_key, 'Regular')
    if italic:
        if style_name == 'Regular':
            style_name = 'Italic'
        else:
            style_name += ' Italic'
    return style_name


def build_postscript_name(family_name, style_name):
    """Build string to use for postscript*Name from family and style names."""

    return '%s-%s' % (family_name.replace(' ', ''),
                      style_name.replace(' ', ''))


def get_weight_code(style_name):
    """Get the appropriate OS/2 weight code for this style."""

    return {
        'Thin': 250,
        'Light': 300,
        'Medium': 500,
        'SemiBold': 600,
        'Bold': 700,
        'ExtraBold': 800,
        'Black': 900
    }.get(style_name, 400)


def get_width_code(style_name):
    """Get the appropriate OS/2 width code for this style."""

    return {
        'Condensed': 3,
        'SemiCondensed': 4
    }.get(style_name, 5)


def rfont_style_to_layer_style(rfont):
    """Convert style as stored in RFonts into Glyphs layer data.

    We store style info in RFonts as:
      familyName: "[family] [condensed]"
      styleName: "[weight] [italic]"
    where "Regular Italic" in styleName becomes simply "Italic".

    Glyphs layer styles are stored as "[weight] [condensed] [italic]", where
    "Regular Condensed" becomes "Condensed" but "Regular Italic" does not get
    shortened.
    """

    style = rfont.info.styleName.split()
    family = rfont.info.familyName.split()
    if style[0] == 'Italic':
        style.insert(0, 'Regular')
    if family[-1] == 'Condensed':
        if style[0] == 'Regular':
            style[0] = 'Condensed'
        else:
            if style[-1] == 'Italic':
                style.insert(-1, 'Condensed')
            else:
                style.append('Condensed')
    return ' '.join(style)


def to_rf_time(datetime_obj):
    """Format a datetime object as specified for UFOs."""
    return datetime_obj.strftime('%Y/%m/%d %H:%M:%S')


def parse_custom_params(data, misc_keys):
    """Parse customParameters into a list of <name, val> pairs."""

    params = []
    for p in data.get('customParameters', []):
        params.append((p.pop('name'), p.pop('value')))
    for key in misc_keys:
        try:
            val = data.pop(key)
        except KeyError:
            continue
        params.append((key, val))
    return params


def load_kerning(rkerning, kerning_data):
    """Add .glyphs kerning to an RKerning object."""

    for left, pairs in kerning_data.items():
        for right, kerning_val in pairs.items():
            rkerning[left, right] = kerning_val


def load_glyph_libdata(rglyph, layer):
    """Add to an RGlyph's lib data."""

    set_robofont_guidelines(rglyph, layer)
    set_robofont_glyph_background(rglyph, layer)
    for key in ['annotations', 'hints']:
        try:
            value = layer.pop(key)
        except KeyError:
            continue
        rglyph.lib[GLYPHS_PREFIX + key] = value

    # data related to components stored in lists of booleans
    # each list's elements correspond to the components in order
    for key in ['disableAlignment', 'locked']:
        values = [c.pop(key, False) for c in layer.get('components', [])]
        if any(values):
            key = key[0].upper() + key[1:]
            rglyph.lib['%scomponents%s' % (GLYPHS_PREFIX, key)] = values


def load_glyph(rglyph, layer, glyph_data):
    """Add .glyphs metadata, paths, components, and anchors to an RGlyph."""

    uval = glyph_data.get('unicode')
    if uval is not None:
        rglyph.unicode = uval
    last_change = glyph_data.get('lastChange')
    if last_change is not None:
        rglyph.lib[GLYPHS_PREFIX + 'lastChange'] = to_rf_time(last_change)

    for key in ['leftMetricsKey', 'rightMetricsKey', 'widthMetricsKey']:
        prefix = GLYPHS_PREFIX + 'Glyphs.'
        try:
            rglyph.lib[prefix + key] = layer.pop(key)
        except KeyError:
            glyph_metrics_key = glyph_data.get(key)
            if glyph_metrics_key:
                rglyph.lib[prefix + key] = glyph_metrics_key

    # load width before background, which is loaded with lib data
    rglyph.width = layer.pop('width')
    load_glyph_libdata(rglyph, layer)

    pen = rglyph.getPointPen()
    draw_paths(pen, layer.get('paths', []))
    draw_components(pen, layer.get('components', []))
    add_anchors_to_glyph(rglyph, layer.get('anchors', []))


def draw_paths(pen, paths):
    """Draw .glyphs paths onto a pen."""

    for path in paths:
        pen.beginPath()
        if not path.pop('closed', False):
            x, y, node_type, smooth = path['nodes'].pop(0)
            assert node_type == 'LINE', 'Open path starts with off-curve points'
            pen.addPoint((x, y), 'move')
        for x, y, node_type, smooth in path.pop('nodes'):
            if node_type not in ['line', 'curve']:
                node_type = None
            pen.addPoint((x, y), node_type, smooth)
        pen.endPath()


def draw_components(pen, components):
    """Draw .glyphs components onto a pen, adding them to the parent RGlyph."""

    for component in components:
        pen.addComponent(component.pop('name'),
                         component.pop('transform', (1, 0, 0, 1, 0, 0)))


def add_anchors_to_glyph(glyph, anchors):
    """Add .glyphs anchors to an RGlyph."""

    for anchor in anchors:
        glyph.appendAnchor(anchor.pop('name'), anchor.pop('position'))


def add_glyph_to_groups(kerning_groups, glyph_data):
    """Add a glyph to its kerning groups, creating new groups if necessary."""

    glyph_name = glyph_data['glyphname']
    group_keys = {
        'L': 'rightKerningGroup',
        'R': 'leftKerningGroup'}
    for side in 'LR':
        group_key = group_keys[side]
        if group_key not in glyph_data:
            continue
        #TODO(jamesgk) figure out if this is a general rule for group naming
        group = '@MMK_%s_%s' % (side, glyph_data.pop(group_key))
        kerning_groups[group] = kerning_groups.get(group, []) + [glyph_name]


def add_groups_to_rfont(rfont, kerning_groups):
    """Add kerning groups to an RFont."""

    for name, glyphs in kerning_groups.items():
        rfont.groups[name] = glyphs


def add_features_to_rfont(rfont, feature_prefixes, classes, features):
    """Write an RFont's OpenType feature file."""

    prefix_str = '\n\n'.join(
        '# Prefix: %s\n%s' % (name, code.strip())
        for name, code in feature_prefixes)

    class_str = '\n\n'.join(
        '%s@%s = [ %s ];' % ('# automatic\n' if automatic else '', name, code)
        for name, code, automatic in classes)

    feature_defs = []
    for name, code, automatic, disabled, notes in features:
        code = code.strip()
        lines = ['feature %s {' % name]
        if notes:
            lines.append('# notes:')
            lines.extend('# ' + line for line in notes.splitlines())
        if automatic:
            lines.append('# automatic')
        if disabled:
            lines.append('# disabled')
            lines.extend('#' + line for line in code.splitlines())
            # empty features cause makeotf to fail, but empty instructions are fine
            # so insert an empty instruction into any empty feature definitions
            lines.append(';')
        else:
            # see previous comment
            if not code:
                code = ';'
            lines.append(code)
        lines.append('} %s;' % name)
        feature_defs.append('\n'.join(lines))
    fea_str = '\n\n'.join(feature_defs)

    rfont.features.text = '\n\n'.join([prefix_str, class_str, fea_str])