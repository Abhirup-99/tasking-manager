import {
  formatChartData,
  formatTimelineData,
  formatTimelineTooltip,
  formatTooltip,
} from '../formatChartJSData';
import { CHART_COLOURS } from '../../config';
import { projectContributionsByDay } from '../../network/tests/mockData/contributions';

describe('formatChartData', () => {
  let reference = [
    {
      label: 'Building',
      field: 'total_building_count_add',
      backgroundColor: CHART_COLOURS.red,
      borderColor: '#000',
    },
    { label: 'Roads', field: 'total_road_km_add', backgroundColor: CHART_COLOURS.green },
    {
      label: 'Points of interests',
      field: 'total_poi_count_add',
      backgroundColor: CHART_COLOURS.orange,
    },
    { label: 'Waterways', field: 'total_waterway_count_add', backgroundColor: CHART_COLOURS.blue },
  ];
  const stats = {
    total_building_count_add: 40,
    total_road_km_add: 60,
    total_poi_count_add: 17,
    total_waterway_count_add: 83,
  };

  it('return the correct information', () => {
    expect(formatChartData(reference, stats)).toEqual({
      datasets: [
        {
          data: [20, 30, 9, 42],
          backgroundColor: [
            CHART_COLOURS.red,
            CHART_COLOURS.green,
            CHART_COLOURS.orange,
            CHART_COLOURS.blue,
          ],
          borderColor: ['#000', undefined, undefined, undefined],
        },
      ],
      labels: ['Building', 'Roads', 'Points of interests', 'Waterways'],
    });
  });
});

describe('formatTimelineData', () => {
  it('return the correct information about the datasets', () => {
    expect(formatTimelineData(projectContributionsByDay.stats, '#fff', '#092')).toEqual({
      datasets: [
        {
          data: [0, 6, 19],
          backgroundColor: '#092',
          borderColor: '#092',
          fill: false,
          label: 'Validated tasks',
        },
        {
          data: [6, 13, 31],
          backgroundColor: '#fff',
          borderColor: '#fff',
          fill: false,
          label: 'Mapped tasks',
        },
      ],
      labels: ['2020-05-19', '2020-06-01', '2020-06-26'],
    });
  });
});

describe('formatTimelineTooltip', () => {
  const tooltipItem = {
    xLabel: '2020-06-26',
    yLabel: 18,
    label: '2020-06-26',
    value: '18',
    index: 2,
    datasetIndex: 1,
    x: 1074.8309643713924,
    y: 78.45394354462593,
  };
  const data = {
    datasets: [
      {
        data: [0, 6, 19],
        backgroundColor: '#092',
        borderColor: '#092',
        fill: false,
        label: 'Validated tasks',
      },
      {
        data: [6, 13, 31],
        backgroundColor: '#fff',
        borderColor: '#fff',
        fill: false,
        label: 'Mapped tasks',
      },
    ],
    labels: ['2020-05-19', '2020-06-01', '2020-06-26'],
  };
  it('returns correct information for Mapped tasks', () => {
    expect(formatTimelineTooltip(tooltipItem, data, true)).toBe('Mapped tasks: 31%');
    expect(formatTimelineTooltip(tooltipItem, data)).toBe('Mapped tasks: 31');
  });
  it('returns correct information for Validated tasks', () => {
    const tooltipItem2 = {
      xLabel: '2020-06-26',
      yLabel: 18,
      label: '2020-06-26',
      value: '18',
      index: 0,
      datasetIndex: 0,
      x: 1074.8309643713924,
      y: 78.45394354462593,
    };
    expect(formatTimelineTooltip(tooltipItem2, data, true)).toBe('Validated tasks: 0%');
    expect(formatTimelineTooltip(tooltipItem2, data)).toBe('Validated tasks: 0');
  });
});

describe('formatTooltip', () => {
  const tooltipItem = {
    xLabel: '',
    yLabel: '',
    label: '',
    value: '',
    index: 1,
    datasetIndex: 0,
    x: 173.3499984741211,
    y: 124,
  };
  const data = {
    datasets: [
      {
        data: [20, 30, 9, 42],
        backgroundColor: [
          CHART_COLOURS.red,
          CHART_COLOURS.green,
          CHART_COLOURS.orange,
          CHART_COLOURS.blue,
        ],
      },
    ],
    labels: ['Building', 'Roads', 'Points of interests', 'Waterways'],
  };
  it('returns correct text with 30 percent', () => {
    expect(formatTooltip(tooltipItem, data)).toBe('Roads: 30%');
  });
  it('returns correct text with 42 percent', () => {
    const tooltipItem = {
      xLabel: '',
      yLabel: '',
      label: '',
      value: '',
      index: 3,
      datasetIndex: 0,
      x: 173.3499984741211,
      y: 124,
    };
    expect(formatTooltip(tooltipItem, data)).toBe('Waterways: 42%');
  });
});
